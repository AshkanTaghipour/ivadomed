import copy
import pytest
import torch
import torch.backends.cudnn as cudnn

from ivadomed import models as imed_models

cudnn.benchmark = True

N_METADATA = 1
OUT_CHANNEL = 1
INITIAL_LR = 0.001

@pytest.mark.parametrize('fraction', [0.1, 0.3, 0.5])
@pytest.mark.parametrize('path_model', ['testing_data/model_unet_test.pt'])
def test_transfer_learning(path_model, fraction, tolerance=0.1):
    device = torch.device("cpu")
    print("Working on {}.".format('cpu'))

    film_bool = bool(sum(film_layers))

    if film_bool:
        n_metadata = N_METADATA

    # Traditional U-Net model
    in_channel = 1

    # Load pretrained model
    model = torch.load(path_model, map_location=device)

    # Deep copy of the model
    model_copy = copy.deepcopy(model)

    # Set model for retrain
    model = imed_models.set_model_for_retrain(model, fraction)

    print('\nSet fraction to retrain: ' + str(fraction))

    # Check Frozen part
    grad_list = [param.requires_grad for name, param in model.named_parameters()]
    fraction_retrain_measured = sum(grad_list) * 1.0 / len(grad_list)
    print('\nMeasure: retrained fraction of the model: ' + str(round(fraction_retrain_measured, 1)))
    # for name, param in model.named_parameters():
    #    print("\t", name, param.requires_grad)
    assert (abs(fraction_retrain_measured - fraction) <= tolerance)
    total_params = sum(p.numel() for p in model.parameters())
    print(f'{total_params:,} total parameters.')
    total_trainable_params = sum(
        p.numel() for p in model.parameters() if p.requires_grad)
    print(f'{total_trainable_params:,} parameters to retrain.')
    assert (total_params > total_trainable_params)

    # Check reset weights
    reset_list = [(p1.data.ne(p2.data).sum() > 0).cpu().numpy() \
                  for p1, p2 in zip(model_copy.parameters(), model.parameters())]
    reset_measured = sum(reset_list) * 1.0 / len(reset_list)
    print('\nMeasure: reset fraction of the model: ' + str(round(reset_measured, 1)))
    assert (abs(reset_measured - fraction) <= tolerance)
    # weights_reset = False
    # for name_p1, p2 in zip(model_copy.named_parameters(), model.parameters()):
    #    if name_p1[1].data.ne(p2.data).sum() > 0:
    #        print('\t', name_p1[0], True)
    #        weights_reset = True
    #    else:
    #        print('\t', name_p1[0], False)
    # assert(weights_reset)
