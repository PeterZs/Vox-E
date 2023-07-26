#!/bin/bash
echo "Starting Run!"

# Reading arguments:
gpu_num=0
while getopts g:a: flag
do
    case "${flag}" in
        g) gpu_num=${OPTARG};;
    a) hf_auth_token_in=${OPTARG};;
    esac
done

# Setting GPU:
echo "Running on GPU: $gpu_num";
export CUDA_VISIBLE_DEVICES=$gpu_num

# Rendering function template:
train_default() {
	# Train:
	# python refine_edited_relu_field.py \
	# -d ./data/${1}/ \
	# -o logs/rf/${1}/${3}/ \
	# -r logs/rf/${1}/ref/saved_models/model_final.pth \
	# -i logs/rf/${1}/${3}/saved_models/model_final.pth \
	# -p "$2" \
	# -eidx ${4} \
	# --log_wandb=False \
	# --hf_auth_token=${5}

	# Rendering Output Video:
	echo "Starting Rendering..."
	python render_sh_based_voxel_grid_attn2.py \
	-i logs/rf/${1}/${3}/saved_models/model_final_refined.pth \
	-ie logs/rf/${1}/${3}/saved_models/model_final_attn_edit.pth \
	-io logs/rf/${1}/${3}/saved_models/model_final_attn_object.pth \
	-p "$2" \
	-eidx ${4} \
	-oidx 5 \
	--save_freq=10 \
	--hf_auth_token=${5} \
	-o output_renders/${1}/${3}_attn_wgt/

	# Rendering Output Video:
	# echo "Starting Rendering..."
	# python render_sh_based_voxel_grid.py \
	# -i logs/rf/${1}/${3}/saved_models/model_final.pth \
	# -o output_renders/${1}/${3}_unrefined/ \
	# --save_freq=10 

	# # Rendering Output Video:
	# echo "Starting Rendering..."
	# python render_sh_based_voxel_grid_attn.py \
	# -i logs/rf/${1}/${3}/saved_models/model_final_refined.pth \
	# -o output_renders/${1}/${3}_refined/ \
	# --save_freq=10 
}

# STARTING RUN:

scene=dog2
prompt="a render of a dog with a party hat"
log_name="party_hat2"
eidx=9
oidx=5
hf_auth_token=$hf_auth_token_in

train_default $scene "$prompt" $log_name $eidx $hf_auth_token