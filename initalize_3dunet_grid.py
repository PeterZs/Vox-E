from pathlib import Path

import click
import torch
from torch import Tensor
import wandb
import copy
from datetime import datetime
from easydict import EasyDict
from torch.backends import cudnn

from thre3d_atom.data.datasets import PosedImagesDataset
from thre3d_atom.modules.unet3d_trainer import train_3dunet_vox_grid_vol_mod_with_posed_images
from thre3d_atom.modules.volumetric_model import (
    VolumetricModel,
    create_volumetric_model_from_saved_model,
)

from thre3d_atom.thre3d_reprs.renderers import (
    render_sh_voxel_grid,
    SHVoxGridRenderConfig,
)

from thre3d_atom.utils.imaging_utils import CameraPose

from thre3d_atom.utils.constants import (
    CAMERA_BOUNDS,
    CAMERA_INTRINSICS,
    HEMISPHERICAL_RADIUS,
)

from thre3d_atom.thre3d_reprs.voxels_3dunet import VoxelGrid3dUnet
from thre3d_atom.thre3d_reprs.voxels import VoxelSize, VoxelGridLocation, create_voxel_grid_from_saved_info_dict
from thre3d_atom.utils.constants import NUM_COLOUR_CHANNELS
from thre3d_atom.utils.logging import log
from thre3d_atom.utils.misc import log_config_to_disk

# Age-old custom option for fast training :)
cudnn.benchmark = True
# Also set torch's multiprocessing start method to spawn
# refer -> https://github.com/pytorch/pytorch/issues/40403
# for more information. Some stupid PyTorch stuff to take care of
torch.multiprocessing.set_start_method("spawn")

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# -------------------------------------------------------------------------------------
#  Command line configuration for the script                                          |
# -------------------------------------------------------------------------------------
# fmt: off
# noinspection PyUnresolvedReferences
@click.command()

# Required arguments:
@click.option("-d", "--data_path", type=click.Path(file_okay=False, dir_okay=True),
              required=True, help="path to the input dataset")
@click.option("-i", "--high_res_model_path", type=click.Path(file_okay=True, dir_okay=False),
              required=True, help="path to the pre-trained high-res model")
@click.option("-o", "--output_path", type=click.Path(file_okay=False, dir_okay=True),
              required=True, help="path for training output")

# Input dataset related arguments:
@click.option("--separate_train_test_folders", type=click.BOOL, required=False,
              default=True, help="whether the data directory has separate train and test folders", 
              show_default=True)
@click.option("--data_downsample_factor", type=click.FloatRange(min=1.0), required=False,
              default=4.0, help="downscale factor for the input images if needed."
                                "Note the default, for training NeRF-based scenes", show_default=True)

# Voxel-grid related arguments:
@click.option("--grid_dims", type=click.INT, nargs=3, required=False, default=(128, 128, 128),
              help="dimensions (#voxels) of the grid along x, y and z axes", show_default=True)
@click.option("--grid_location", type=click.FLOAT, nargs=3, required=False, default=(0.0, 0.0, 0.0),
              help="dimensions (#voxels) of the grid along x, y and z axes", show_default=True)
@click.option("--normalize_scene_scale", type=click.BOOL, required=False, default=False,
              help="whether to normalize the scene's scale to unit radius", show_default=True)
@click.option("--grid_world_size", type=click.FLOAT, nargs=3, required=False, default=(3.0, 3.0, 3.0),
              help="size (extent) of the grid in world coordinate system."
                   "Please carefully note it's use in conjunction with the normalization :)", show_default=True)
@click.option("--sh_degree", type=click.INT, required=False, default=0,
              help="degree of the spherical harmonics coefficients to be used. "
                   "Supported values: [0, 1, 2, 3]", show_default=True)

# -------------------------------------------------------------------------------------
#                        !!! :) MOST IMPORTANT OPTION :) !!!                          |
# -------------------------------------------------------------------------------------
@click.option("--use_relu_field", type=click.BOOL, required=False, default=True,    # |
              help="whether to use relu_fields or revert to traditional grids",     # |
              show_default=True)                                                    # |              
# -------------------------------------------------------------------------------------

@click.option("--use_softplus_field", type=click.BOOL, required=False, default=True,
              help="whether to use softplus_field or relu_field", show_default=True)

# Rendering related arguments:
@click.option("--render_num_samples_per_ray", type=click.INT, required=False, default=1024,
              help="number of samples taken per ray during rendering", show_default=True)
@click.option("--parallel_rays_chunk_size", type=click.INT, required=False, default=32768,
              help="number of parallel rays processed on the GPU for accelerated rendering", show_default=True)
@click.option("--white_bkgd", type=click.BOOL, required=False, default=True,
              help="whether to use white background for training with synthetic (background-less) scenes :)",
              show_default=True)  # this option is also used in pre-processing the dataset

# Training related arguments:
@click.option("--ray_batch_size", type=click.INT, required=False, default=65536,
              help="number of randomly sampled rays used per training iteration", show_default=True)
@click.option("--train_num_samples_per_ray", type=click.INT, required=False, default=256,
              help="number of samples taken per ray during training", show_default=True)
@click.option("--num_stages", type=click.INT, required=False, default=1,
              help="number of progressive growing stages used in training", show_default=True)
@click.option("--num_iterations_per_stage", type=click.INT, required=False, default=5000,
              help="number of training iterations performed per stage", show_default=True)
@click.option("--scale_factor", type=click.FLOAT, required=False, default=2.0,
              help="factor by which the grid is up-scaled after each stage", show_default=True)
@click.option("--learning_rate", type=click.FLOAT, required=False, default=0.001,
              help="learning rate used at the beginning (ADAM OPTIMIZER)", show_default=True)
@click.option("--lr_decay_steps_per_stage", type=click.INT, required=False, default=5000*100,
              help="number of iterations after which lr is exponentially decayed per stage", show_default=True)
@click.option("--lr_decay_gamma_per_stage", type=click.FLOAT, required=False, default=0.1,
              help="value of gamma for exponential lr_decay (happens per stage)", show_default=True)
@click.option("--stagewise_lr_decay_gamma", type=click.FLOAT, required=False, default=0.9,
              help="value of gamma used for reducing the learning rate after each stage", show_default=True)
@click.option("--apply_diffuse_render_regularization", type=click.BOOL, required=False, default=True,
              help="whether to apply the diffuse render regularization."
                   "this is a weird conjure of mine, where we ask the diffuse render "
                   "to match, as closely as possible, the GT-possibly-specular one :D"
                   "can be off or on, on yields stabler training :) ", show_default=False)
@click.option("--num_workers", type=click.INT, required=False, default=4,
              help="number of worker processes used for loading the data using the dataloader"
                   "note that this will be ignored if GPU-caching of the data is successful :)", show_default=True)

# Various frequencies:
@click.option("--save_frequency", type=click.INT, required=False, default=250,
              help="number of iterations after which a model is saved", show_default=True)
@click.option("--test_frequency", type=click.INT, required=False, default=250,
              help="number of iterations after which test metrics are computed", show_default=True)
@click.option("--feedback_frequency", type=click.INT, required=False, default=100,
              help="number of iterations after which rendered feedback is generated", show_default=True)
@click.option("--summary_frequency", type=click.INT, required=False, default=50,
              help="number of iterations after which training-loss/other-summaries are logged", show_default=True)

# Miscellaneous modes
@click.option("--verbose_rendering", type=click.BOOL, required=False, default=False,
              help="whether to show progress while rendering feedback during training"
                   "can be turned-off when running on server-farms :D", show_default=True)
@click.option("--fast_debug_mode", type=click.BOOL, required=False, default=False,
              help="whether to use the fast debug mode while training "
                   "(skips testing and some lengthy visualizations)", show_default=True)

# sds specific stuff
@click.option("--diffuse_weight", type=click.FLOAT, required=False, default=0.0000001,
              help="diffuse weight used for regularization", show_default=True)
@click.option("--specular_weight", type=click.FLOAT, required=False, default=0.0000001,
              help="specular weight used for regularization", show_default=True)
@click.option("--directional_dataset", type=click.BOOL, required=False, default=False,
              help="whether to use a directional dataset for SDS where each view comes with a direction",
               show_default=True)
@click.option("--use_uncertainty", type=click.BOOL, required=False, default=False,
              help="whether to use an uncertainty aware type loss",
               show_default=True)
@click.option("--new_frame_frequency", type=click.INT, required=False, default=1,
              help="number of iterations where we work on the same pose", show_default=True)
@click.option("--gvg_weight", type=click.FLOAT, required=False, default=0.0,
              help="grid vs grid loss weight", show_default=True)
# fmt: on
# -------------------------------------------------------------------------------------

def main(**kwargs) -> None:
    # load the requested configuration for the training
    config = EasyDict(kwargs)

    # parse os-checked path-strings into Pathlike Paths :)
    model_path = Path(config.high_res_model_path)
    output_path = Path(config.output_path)
    output_path.mkdir(exist_ok=True, parents=True)

    data_path = Path(config.data_path)
    if config.separate_train_test_folders:
        train_dataset, test_dataset = (
            PosedImagesDataset(
                images_dir=data_path / mode,
                camera_params_json=data_path / f"{mode}_camera_params.json",
                normalize_scene_scale=config.normalize_scene_scale,
                downsample_factor=config.data_downsample_factor,
                rgba_white_bkgd=config.white_bkgd,
            )
            for mode in ("train", "test")
        )
    else:
        train_dataset = PosedImagesDataset(
            images_dir=data_path / "images",
            camera_params_json=data_path / "camera_params.json",
            normalize_scene_scale=config.normalize_scene_scale,
            downsample_factor=config.data_downsample_factor,
            rgba_white_bkgd=config.white_bkgd,
        )
    
    pretrained_vol_mod, _ = create_volumetric_model_from_saved_model(
        model_path=model_path,
        thre3d_repr_creator=create_voxel_grid_from_saved_info_dict,
        device=device,
    )

    # set up 3d Unet vox grid
    vox_grid_density_activations_dict = {
            "density_preactivation": torch.abs,
            "density_postactivation": torch.nn.Identity(),
            "expected_density_scale": 1.0,  # Also note this expected density value :wink:
        }
    voxel_size = VoxelSize(*[dim_size / grid_dim for dim_size, grid_dim
                             in zip(config.grid_world_size, config.grid_dims)])
    unet3d_vox_grid = VoxelGrid3dUnet(densities=pretrained_vol_mod.thre3d_repr._densities,
                                     features=pretrained_vol_mod.thre3d_repr._features,
                                     voxel_size=voxel_size,
                                     grid_location=VoxelGridLocation(*config.grid_location),
                                     **vox_grid_density_activations_dict,
                                     tunable=True,)
    
    print("Starting Unet initialization")
    #train_3dunet_vox_grid_to_copy_pretrained(unet3d_vox_grid,
    #                                         pretrained_vol_mod.thre3d_repr._densities,
    #                                         pretrained_vol_mod.thre3d_repr._features)

    unet3d_vol_mod = VolumetricModel(
        thre3d_repr=unet3d_vox_grid,
        render_procedure=render_sh_voxel_grid,
        render_config=SHVoxGridRenderConfig(
            num_samples_per_ray=config.train_num_samples_per_ray,
            camera_bounds=train_dataset.camera_bounds,
            white_bkgd=config.white_bkgd,
            render_num_samples_per_ray=config.render_num_samples_per_ray,
            parallel_rays_chunk_size=config.parallel_rays_chunk_size,
        ),
        device=device,
    )

    train_3dunet_vox_grid_vol_mod_with_posed_images(
        vol_mod_3dunet=unet3d_vol_mod,
        vol_mod_pretrained=pretrained_vol_mod,
        train_dataset=train_dataset,
        output_dir=output_path,
        test_dataset=test_dataset,
        ray_batch_size=config.ray_batch_size,
        num_stages=config.num_stages,
        num_iterations_per_stage=config.num_iterations_per_stage,
        scale_factor=config.scale_factor,
        learning_rate=config.learning_rate,
        lr_decay_gamma_per_stage=config.lr_decay_gamma_per_stage,
        lr_decay_steps_per_stage=config.lr_decay_steps_per_stage,
        stagewise_lr_decay_gamma=config.stagewise_lr_decay_gamma,
        save_freq=config.save_frequency,
        test_freq=config.test_frequency,
        feedback_freq=config.feedback_frequency,
        summary_freq=config.summary_frequency,
        apply_diffuse_render_regularization=config.apply_diffuse_render_regularization,
        num_workers=config.num_workers,
        verbose_rendering=config.verbose_rendering,
        fast_debug_mode=config.fast_debug_mode,
        gvg_weight=config.gvg_weight,
    )

    #camera_bounds, camera_intrinsics = (
    #    train_dataset.camera_bounds,
    #    train_dataset.camera_intrinsics,
    #)
    #torch.save(
    #           unet3d_vol_mod.get_save_info(
    #               extra_info={
    #                   CAMERA_BOUNDS: camera_bounds,
    #                   CAMERA_INTRINSICS: camera_intrinsics,
    #                   HEMISPHERICAL_RADIUS: train_dataset.get_hemispherical_radius_estimate(),
    #               }
    #           ),
    #           output_path / f"initialized_3dunet_voxgrid.pth",
    #        )
    print("Success!")

    #render_feedback_pose = CameraPose(
    #                    rotation=train_dataset[10][1][:, :3].cpu().numpy(),
    #                    translation=train_dataset[10][1][:, 3:].cpu().numpy(),
    #                )
    #visualize_sh_vox_grid_vol_mod_rendered_feedback(
    #                    vol_mod=vox_grid_vol_mod,
    #                    vol_mod_name="sds",
    #                    render_feedback_pose=render_feedback_pose,
    #                    camera_intrinsics=camera_intrinsics,
    #                    global_step=0,
    #                    feedback_logs_dir=output_path,
    #                    parallel_rays_chunk_size=vox_grid_vol_mod.render_config.parallel_rays_chunk_size,
    #                    training_time=0,
    #                    log_diffuse_rendered_version=False,
    #                    use_optimized_sampling_mode=False,  # testing how the optimized sampling mode rendering looks 🙂
    #                    overridden_num_samples_per_ray=vox_grid_vol_mod.render_config.render_num_samples_per_ray,
    #                    verbose_rendering=False,
    #                    log_wandb=False,
    #                )

def train_3dunet_vox_grid_to_copy_pretrained(vox_grid_3du: VoxelGrid3dUnet,
                                             pretrained_features: Tensor,
                                             pretrained_densities: Tensor,
                                             learning_rate: float = 0.015,
                                             feedback_fr: int = 100,
                                             epochs = 3000):
    optimizer = torch.optim.Adam(vox_grid_3du.parameters(), lr=learning_rate)
    loss_fn = torch.nn.MSELoss()
    target_grid = torch.cat([pretrained_densities, pretrained_features], dim=-1)

    # training loop
    for iter in range(epochs):
        optimizer.zero_grad()
        densities, features = vox_grid_3du.get_densities_and_features()
        output = torch.cat([densities, features], dim=-1)
        loss = loss_fn(output, target_grid)
        loss.backward()
        optimizer.step()
        if iter % feedback_fr == 0 or iter == epochs - 1:
            print(f"Iter: {iter}, loss: {loss.item()}")

if __name__ == "__main__":
    main()
