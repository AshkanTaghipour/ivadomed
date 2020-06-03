import time
import pytest
import numpy as np
import torch
import torch.backends.cudnn as cudnn
from torch import optim
from torch.utils.data import DataLoader
from tqdm import tqdm

import ivadomed.transforms as imed_transforms
from ivadomed import losses
from ivadomed import models
from ivadomed import utils as imed_utils
from ivadomed.loader import utils as imed_loader_utils, loader as imed_loader, film as imed_film

cudnn.benchmark = True

GPU_NUMBER = 0
BATCH_SIZE = 8
DROPOUT = 0.4
DEPTH = 3
BN = 0.1
N_EPOCHS = 10
INIT_LR = 0.01
FILM_LAYERS = [0, 0, 0, 0, 0, 1, 1, 1]
PATH_BIDS = 'testing_data'

@pytest.mark.parametrize('train_lst', [['sub-test001']])
@pytest.mark.parametrize('config', [
    {
        "transformation": {"Resample": {"wspace": 0.75, "hspace": 0.75},
                           "ROICrop": {"size": [48, 48]},
                           "NumpyToTensor": {}},
        "roi_params": {"suffix": "_seg-manual", "slice_filter_roi": 10},
        "contrast_params": {"contrast_lst": ['T2w'], "balance": {}},
        "multichannel": False
    },
    {
        "transformation": {"Resample": {"wspace": 0.75, "hspace": 0.75},
                           "ROICrop": {"size": [48, 48]},
                           "NumpyToTensor": {}},
        "roi_params": {"suffix": "_seg-manual", "slice_filter_roi": 10},
        "contrast_params": {"contrast_lst": ['T1w', 'T2w'], "balance": {}},
        "multichannel": True
    },
    {
        "transformation": {"CenterCrop": {"size": [96, 96, 16]},
                           "NumpyToTensor": {}},
        "roi_params": {"suffix": None, "slice_filter_roi": 0},
        "contrast_params": {"contrast_lst": ['T1w', 'T2w'], "balance": {}},
        "multichannel": False
    }
])

def test_unet_time(train_lst, config):
    cuda_available, device = imed_utils.define_device(GPU_NUMBER)

    loader_params = {
        "data_list": train_lst,
        "dataset_type": "training",
        "requires_undo": False,
        "bids_path": PATH_BIDS,
        "target_suffix": target_lst,
        "roi_params": roi_params,
        "model_params": {"name": "Unet"},
        "slice_filter_params": slice_filter_params,
        "slice_axis": "axial",
        "multichannel": False
    }
    # Get Training dataset
    ds_train = imed_loader.load_dataset(**loader_params)



    train_loader = DataLoader(ds_train, batch_size=BATCH_SIZE,
                              shuffle=True, pin_memory=True,
                              collate_fn=imed_loader_utils.imed_collate,
                              num_workers=1)

    multichannel_loader = DataLoader(ds_mutichannel, batch_size=BATCH_SIZE,
                                     shuffle=True, pin_memory=True,
                                     collate_fn=imed_loader_utils.imed_collate,
                                     num_workers=1)
    loader_3d = DataLoader(ds_3d, batch_size=1,
                           shuffle=True, pin_memory=True,
                           collate_fn=imed_loader_utils.imed_collate,
                           num_workers=1)

    model_list = [(models.Unet(depth=DEPTH,
                               film_layers=FILM_LAYERS,
                               n_metadata=len(
                                   [ll for l in train_onehotencoder.categories_ for ll in l]),
                               drop_rate=DROPOUT,
                               bn_momentum=BN,
                               film_bool=True), train_loader, True, 'Filmed-Unet'),
                  (models.Unet(in_channel=1,
                               out_channel=1,
                               depth=2,
                               drop_rate=DROPOUT,
                               bn_momentum=BN), train_loader, False, 'Unet'),
                  (models.Unet(in_channel=2), multichannel_loader, False, 'Multi-Channels Unet'),
                  (models.UNet3D(in_channels=1, n_classes=2 + 1), loader_3d, False, '3D Unet'),
                  (models.UNet3D(in_channels=1, n_classes=2 + 1, attention=True), loader_3d, False, 'Attention UNet')]

    for model, train_loader, film_bool, model_name in model_list:
        print("Training {}".format(model_name))
        if cuda_available:
            model.cuda()

        step_scheduler_batch = False
        optimizer = optim.Adam(model.parameters(), lr=INIT_LR)
        scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, N_EPOCHS)

        load_lst, pred_lst, opt_lst, schedul_lst, init_lst, gen_lst = [], [], [], [], [], []
        for epoch in tqdm(range(1, N_EPOCHS + 1), desc="Training"):
            start_time = time.time()

            start_init = time.time()
            lr = scheduler.get_last_lr()[0]
            model.train()
            tot_init = time.time() - start_init
            init_lst.append(tot_init)

            num_steps = 0
            start_gen = 0
            for i, batch in enumerate(train_loader):
                if i > 0:
                    tot_gen = time.time() - start_gen
                    gen_lst.append(tot_gen)

                start_load = time.time()
                input_samples, gt_samples = batch["input"], batch["gt"]
                if cuda_available:
                    var_input = input_samples.cuda()
                    var_gt = gt_samples.cuda(non_blocking=True)
                else:
                    var_input = input_samples
                    var_gt = gt_samples

                sample_metadata = batch["input_metadata"]
                if film_bool:
                    var_metadata = [train_onehotencoder.transform([sample_metadata[0][k]['film_input']]).tolist()[0]
                                    for k in range(len(sample_metadata[0]))]
                tot_load = time.time() - start_load
                load_lst.append(tot_load)

                start_pred = time.time()
                if film_bool:
                    # Input the metadata related to the input samples
                    preds = model(var_input, var_metadata)
                else:
                    preds = model(var_input)
                tot_pred = time.time() - start_pred
                pred_lst.append(tot_pred)

                start_opt = time.time()
                loss = - losses.dice_loss(preds, var_gt)

                optimizer.zero_grad()
                loss.backward()

                optimizer.step()
                if step_scheduler_batch:
                    scheduler.step()

                num_steps += 1
                tot_opt = time.time() - start_opt
                opt_lst.append(tot_opt)

                start_gen = time.time()

            start_schedul = time.time()
            if not step_scheduler_batch:
                scheduler.step()
            tot_schedul = time.time() - start_schedul
            schedul_lst.append(tot_schedul)

            end_time = time.time()
            total_time = end_time - start_time
            tqdm.write("Epoch {} took {:.2f} seconds.".format(epoch, total_time))

        print('Mean SD init {} -- {}'.format(np.mean(init_lst), np.std(init_lst)))
        print('Mean SD load {} -- {}'.format(np.mean(load_lst), np.std(load_lst)))
        print('Mean SD pred {} -- {}'.format(np.mean(pred_lst), np.std(pred_lst)))
        print('Mean SDopt {} --  {}'.format(np.mean(opt_lst), np.std(opt_lst)))
        print('Mean SD gen {} -- {}'.format(np.mean(gen_lst), np.std(gen_lst)))
        print('Mean SD scheduler {} -- {}'.format(np.mean(schedul_lst), np.std(schedul_lst)))
