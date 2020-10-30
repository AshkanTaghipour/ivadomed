import os
import matplotlib.animation as anim
import matplotlib.pyplot as plt
import numpy as np
import nibabel as nib
import torchvision.utils as vutils
from ivadomed import postprocessing as imed_postpro
from ivadomed import inference as imed_inference
from ivadomed import utils as imed_utils
import torch
import torch.nn.functional as F
from torch.autograd import Variable
from ivadomed import image as imed_image


def overlap_im_seg(img, seg):
    """Overlap image (background, greyscale) and segmentation (foreground, jet)."""
    seg_zero, seg_nonzero = np.where(seg <= 0.1), np.where(seg > 0.1)
    seg_jet = plt.cm.jet(plt.Normalize(vmin=0, vmax=1.)(seg))
    seg_jet[seg_zero] = 0.0
    img_grey = plt.cm.binary_r(plt.Normalize(vmin=np.amin(img), vmax=np.amax(img))(img))
    img_out = np.copy(img_grey)
    img_out[seg_nonzero] = seg_jet[seg_nonzero]
    return img_out


class LoopingPillowWriter(anim.PillowWriter):
    def finish(self):
        self._frames[0].save(
            self.outfile, save_all=True, append_images=self._frames[1:],
            duration=int(1000 / self.fps), loop=0)


class AnimatedGif:
    """Generates GIF.

    Args:
        size (tuple): Size of frames.

    Attributes:
        fig (plt):
        size_x (int):
        size_y (int):
        images (list): List of frames.
    """

    def __init__(self, size):
        self.fig = plt.figure()
        self.fig.set_size_inches(size[0] / 50, size[1] / 50)
        self.size_x = size[0]
        self.size_y = size[1]
        self.ax = self.fig.add_axes([0, 0, 1, 1], frameon=False, aspect=1)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.images = []

    def add(self, image, label=''):
        plt_im = self.ax.imshow(image, cmap='Greys', vmin=0, vmax=1, animated=True)
        plt_txt = self.ax.text(self.size_x * 3 // 4, self.size_y - 10, label, color='red', animated=True)
        self.images.append([plt_im, plt_txt])

    def save(self, filename):
        animation = anim.ArtistAnimation(self.fig, self.images, interval=50, blit=True,
                                         repeat_delay=500)
        animation.save(filename, writer=LoopingPillowWriter(fps=1))


def save_color_labels(gt_data, binarize, gt_filename, output_filename, slice_axis):
    """Saves labels encoded in RGB in specified output file.

    Args:
        gt_data (ndarray): Input image with dimensions (Number of classes, height, width, depth).
        binarize (bool): If True binarizes gt_data to 0 and 1 values, else soft values are kept.
        gt_filename (str): GT path and filename.
        output_filename (str): Name of the output file where the colored labels are saved.
        slice_axis (int): Indicates the axis used to extract slices: "axial": 2, "sagittal": 0, "coronal": 1.

    Returns:
        ndarray: RGB labels.
    """
    n_class, h, w, d = gt_data.shape
    labels = range(n_class)
    # Generate color labels
    multi_labeled_pred = np.zeros((h, w, d, 3))
    if binarize:
        gt_data = imed_postpro.threshold_predictions(gt_data)

    # Keep always the same color labels
    np.random.seed(6)

    for label in labels:
        r, g, b = np.random.randint(0, 256, size=3)
        multi_labeled_pred[..., 0] += r * gt_data[label,]
        multi_labeled_pred[..., 1] += g * gt_data[label,]
        multi_labeled_pred[..., 2] += b * gt_data[label,]

    rgb_dtype = np.dtype([('R', 'u1'), ('G', 'u1'), ('B', 'u1')])
    multi_labeled_pred = multi_labeled_pred.copy().astype('u1').view(dtype=rgb_dtype).reshape((h, w, d))

    imed_inference.pred_to_nib([multi_labeled_pred], [], gt_filename,
                output_filename, slice_axis=slice_axis, kernel_dim='3d', bin_thr=-1, discard_noise=False)

    return multi_labeled_pred


def convert_labels_to_RGB(grid_img):
    """Converts 2D images to RGB encoded images for display in tensorboard.

    Args:
        grid_img (Tensor): GT or prediction tensor with dimensions (batch size, number of classes, height, width).

    Returns:
        tensor: RGB image with shape (height, width, 3).
    """
    # Keep always the same color labels
    batch_size, n_class, h, w = grid_img.shape
    rgb_img = torch.zeros((batch_size, 3, h, w))

    # Keep always the same color labels
    np.random.seed(6)
    for i in range(n_class):
        r, g, b = np.random.randint(0, 256, size=3)
        rgb_img[:, 0, ] = r * grid_img[:, i, ]
        rgb_img[:, 1, ] = g * grid_img[:, i, ]
        rgb_img[:, 2, ] = b * grid_img[:, i, ]

    return rgb_img


def save_tensorboard_img(writer, epoch, dataset_type, input_samples, gt_samples, preds, is_three_dim=False):
    """Saves input images, gt and predictions in tensorboard.

    Args:
        writer (SummaryWriter): Tensorboard's summary writer.
        epoch (int): Epoch number.
        dataset_type (str): Choice between Training or Validation.
        input_samples (Tensor): Input images with shape (batch size, number of channel, height, width, depth) if 3D else
            (batch size, number of channel, height, width)
        gt_samples (Tensor): GT images with shape (batch size, number of channel, height, width, depth) if 3D else
            (batch size, number of channel, height, width)
        preds (Tensor): Model's prediction with shape (batch size, number of channel, height, width, depth) if 3D else
            (batch size, number of channel, height, width)
        is_three_dim (bool): True if 3D input, else False.
    """
    if is_three_dim:
        # Take all images stacked on depth dimension
        num_2d_img = input_samples.shape[-1]
    else:
        num_2d_img = 1
    if isinstance(input_samples, list):
        input_samples_copy = input_samples.copy()
    else:
        input_samples_copy = input_samples.clone()
    preds_copy = preds.clone()
    gt_samples_copy = gt_samples.clone()
    for idx in range(num_2d_img):
        if is_three_dim:
            input_samples = input_samples_copy[..., idx]
            preds = preds_copy[..., idx]
            gt_samples = gt_samples_copy[..., idx]
            # Only display images with labels
            if gt_samples.sum() == 0:
                continue

        # take only one modality for grid
        if not isinstance(input_samples, list) and input_samples.shape[1] > 1:
            tensor = input_samples[:, 0, ][:, None, ]
            input_samples = torch.cat((tensor, tensor, tensor), 1)
        elif isinstance(input_samples, list):
            input_samples = input_samples[0]

        grid_img = vutils.make_grid(input_samples,
                                    normalize=True,
                                    scale_each=True)
        writer.add_image(dataset_type + '/Input', grid_img, epoch)

        grid_img = vutils.make_grid(convert_labels_to_RGB(preds),
                                    normalize=True,
                                    scale_each=True)

        writer.add_image(dataset_type + '/Predictions', grid_img, epoch)

        grid_img = vutils.make_grid(convert_labels_to_RGB(gt_samples),
                                    normalize=True,
                                    scale_each=True)

        writer.add_image(dataset_type + '/Ground Truth', grid_img, epoch)


def save_feature_map(batch, layer_name, log_directory, model, test_input, slice_axis):
    """Save model feature maps.

    Args:
        batch (dict):
        layer_name (str):
        log_directory (str): Output folder.
        model (nn.Module): Network.
        test_input (Tensor):
        slice_axis (int): Indicates the axis used for the 2D slice extraction: Sagittal: 0, Coronal: 1, Axial: 2.
    """
    if not os.path.exists(os.path.join(log_directory, layer_name)):
        os.mkdir(os.path.join(log_directory, layer_name))

    # Save for subject in batch
    for i in range(batch['input'].size(0)):
        inp_fmap, out_fmap = \
            imed_utils.HookBasedFeatureExtractor(model, layer_name, False).forward(Variable(test_input[i][None,]))

        # Display the input image and Down_sample the input image
        orig_input_img = test_input[i][None,].cpu().numpy()
        upsampled_attention = F.interpolate(out_fmap[1],
                                            size=test_input[i][None,].size()[2:],
                                            mode='trilinear',
                                            align_corners=True).data.cpu().numpy()

        path = batch["input_metadata"][0][i]["input_filenames"]

        basename = path.split('/')[-1]
        save_directory = os.path.join(log_directory, layer_name, basename)

        # Write the attentions to a nifti image
        nib_ref = nib.load(path)
        nib_ref_can = nib.as_closest_canonical(nib_ref)
        oriented_image = imed_image.reorient_image(orig_input_img[0, 0, :, :, :], slice_axis, nib_ref, nib_ref_can)

        nib_pred = nib.Nifti1Image(oriented_image, nib_ref.affine)
        nib.save(nib_pred, save_directory)

        basename = basename.split(".")[0] + "_att.nii.gz"
        save_directory = os.path.join(log_directory, layer_name, basename)
        attention_map = imed_image.reorient_image(upsampled_attention[0, 0, :, :, :], slice_axis, nib_ref, nib_ref_can)
        nib_pred = nib.Nifti1Image(attention_map, nib_ref.affine)

        nib.save(nib_pred, save_directory)
