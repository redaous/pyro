import argparse
import torch
from torch.nn.functional import softplus
from torch.distributions.transforms import AffineTransform, SigmoidTransform
import numpy as np

import pyro
from pyro import optim
import pyro.distributions as dist
from pyro.infer import SVI, Trace_ELBO
from pyro.contrib.oed.eig import barber_agakov_ape
from pyro.contrib.oed.util import rmv

from models.bayes_linear import sigmoid_model, rf_group_assignments

from ba.guide import Ba_sigmoid_guide

# Random effects designs
AB_test_reff_6d_10n_12p, AB_sigmoid_design_6d = rf_group_assignments(10)

sigmoid_ba_guide = lambda d: Ba_sigmoid_guide(torch.tensor([10., 2.5]), d, 10, {"w1": 2}).guide
# svi_ba_guide = lambda: Ba_sigmoid_guide(torch.ones(12), 1, 10, {"w": 12}).guide


def true_model(design):
    w1 = torch.tensor([-1., 1.])
    w2 = torch.tensor([-.5, .5, -.5, .5, -.5, 2., -2., 2., -2., 0.])
    w = torch.cat([w1, w2], dim=-1)
    k = torch.tensor(.1)
    response_mean = rmv(design, w)

    base_dist = dist.Normal(response_mean, torch.tensor(1.)).independent(1)
    k = k.expand(response_mean.shape)
    transforms = [AffineTransform(loc=0., scale=k), SigmoidTransform()]
    response_dist = dist.TransformedDistribution(base_dist, transforms)
    return pyro.sample("y", response_dist)


def svi_guide(design):
    # A highly inflexible and inaccurate guide, but works for this
    # example
    batch_shape = design.shape[:-2]
    n, p = design.shape[-2:]
    k_alpha = 100.*torch.ones(batch_shape + (n,))
    k_beta = 1000.*torch.ones(batch_shape + (n,))
    pyro.sample("k", dist.Gamma(k_alpha, k_beta).independent(1))

    w1_mean = pyro.param("w1_mean", torch.zeros(batch_shape + (2,)))
    w1_sds = softplus(pyro.param("w1_sds", -5.*torch.ones(batch_shape + (2,))))
    pyro.sample("w1", dist.Normal(w1_mean, w1_sds).independent(1))
    w2_mean = torch.tensor(0.)
    w2_sds = torch.tensor([1.]*5 + [10.]*5)
    pyro.sample("w2", dist.Normal(w2_mean, w2_sds).independent(1))


def learn_posterior(y, d, model, svi_guide):
    guide = lambda d: svi_guide({"y": y}, d, None, ["w"])
    vi_parameters = {
        "guide": guide, 
        "optim": optim.Adam({"lr": 0.005}),
        "loss": Trace_ELBO(),
        "num_steps": 10000}
    conditioned_model = pyro.condition(model, data={"y": y})
    SVI(conditioned_model, **vi_parameters).run(d)

    print(pyro.param("w1_mean"))
    # print(pyro.param("w2_mean"))
    
    new_model = sigmoid_model(pyro.param("w1_mean"),
                              pyro.param("w1_sds"),
                              pyro.param("w2_mean"),
                              pyro.param("w2_sds"),
                              torch.tensor(1.),
                              100.*torch.ones(10),
                              1000.*torch.ones(10),
                              AB_sigmoid_design_6d)
    return new_model


def main():

    results = {'oed': [], 'rand': []}

    for typ in ['oed', 'rand']:
        print("Type", typ)

        for k in range(5):
            print("Run", k)

            model = sigmoid_model(torch.tensor(0.), torch.tensor([10., 2.5]), torch.tensor(0.),
                                  torch.tensor([1.]*5 + [10.]*5), torch.tensor(1.),
                                  100.*torch.ones(10), 1000.*torch.ones(10), AB_sigmoid_design_6d)
            my_guide = sigmoid_ba_guide(6)
            ba_kwargs = {"num_samples": 100, "num_steps": 500, "guide": my_guide, 
                         "optim": optim.Adam({"lr": 0.05}), "final_num_samples": 500}

            for experiment_number in range(1, 6):
                pyro.clear_param_store()
                print("Experiment number", experiment_number)

                estimation_surface = barber_agakov_ape(model, AB_test_reff_6d_10n_12p, "y", "w1", **ba_kwargs)
                # print(estimation_surface)

                # Run experiment
                if typ == 'oed':
                    d_star_index = torch.argmin(estimation_surface)
                elif typ == 'rand':
                    d_star_index = torch.randint(6, tuple())
                d_star_index = int(d_star_index)
                design = AB_test_reff_6d_10n_12p[d_star_index, ...]
                y = true_model(design)
                mu, scale_tril = my_guide({"y": y}, AB_test_reff_6d_10n_12p, None, ["w1"])
                
                model = sigmoid_model(mu[d_star_index, ...].detach(), torch.diag(scale_tril[d_star_index, ...].detach()), torch.tensor(0.),
                                      torch.tensor([1.]*5 + [10.]*5), torch.tensor(1.),
                                      100.*torch.ones(10), 1000.*torch.ones(10), AB_sigmoid_design_6d)

            results[typ].append((mu[d_star_index, ...].detach().numpy(), scale_tril[d_star_index, ...].detach().numpy()))

    print(results)
    import matplotlib.pyplot as plt
    plt.figure()
    x = np.arange(1, 5)
    plt.plot(x, np.array([results['oed'][i] for i in range(5)]), color='o')
    plt.plot(x, np.array([results['oed'][i] for i in range(5)]), color='b')
    plt.axhline(1)
    plt.axhline(-1)
    plt.show()



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sigmoid iterated experiment design")
    args = parser.parse_args()
    main()