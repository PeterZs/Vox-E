#!/bin/bash
echo "Starting Run!"

# Reading arguments:
gpu_num=0
while getopts g:d: flag
do
    case "${flag}" in
        g) gpu_num=${OPTARG};;
		d) sweep_name_in=${OPTARG};;
    esac
done

# Setting GPU:
echo "Running on GPU: $gpu_num";
export CUDA_VISIBLE_DEVICES=$gpu_num

# Rendering function template:
train_default() {
	# Train:
	python edit_pretrained_relu_field.py \
	-d ./data/${1}/ \
	-o logs/rf/${2}/${1}/${4} \
	-i logs/rf/${1}/ref/saved_models/model_final.pth \
	-p "$3" \
	-eidx=${5} \
	--do_refinement=False \
	--log_wandb=True \
	--learning_rate=0.028 \
	--post_process_scc=True \
	--sh_degree=0 # we currently only support diffuse

	# Rendering Output Video:
	echo "Starting Rendering..."
	python render_sh_based_voxel_grid.py \
	-i logs/rf/${2}/${1}/${4}/saved_models/model_final.pth \
	-o output_renders/${2}/${1}/${4}/ \
	--sds_prompt="$3" \
	--save_freq=10
}

# STARTING RUN:
#
#sweep_name=sweep_full_global_extended
#scene=alien
#prompt="a render of a yarn doll of a grey alien"
#log_name="yarn"
#eidx=9
#
#train_default $scene $sweep_name "$prompt" $log_name $eidx
#
#sweep_name=sweep_full_global_extended
#scene=alien
#prompt="a render of a wood carving of an alien"
#log_name="wood"
#eidx=9
#
#train_default $scene $sweep_name "$prompt" $log_name $eidx
#
#sweep_name=sweep_full_global_extended
#scene=alien
#prompt="a render of a grey claymation alien"
#log_name="claymation"
#eidx=8


sweep_name=sweep_full_global_extended
scene=duck
prompt="a render of a yarn doll of a duck"
log_name="yarn"
eidx=9

train_default $scene $sweep_name "$prompt" $log_name $eidx

sweep_name=sweep_full_global_extended
scene=duck
prompt="a render of a wood carving of a duck"
log_name="wood"
eidx=9

train_default $scene $sweep_name "$prompt" $log_name $eidx

sweep_name=sweep_full_global_extended
scene=duck
prompt="a render of a claymation duck"
log_name="claymation"
eidx=9

train_default $scene $sweep_name "$prompt" $log_name $eidx

#sweep_name=sweep_full_global_extended
#scene=alien
#prompt="a render of a white claymation alien"
#log_name="claymation"
#eidx=8
#
#train_default $scene $sweep_name "$prompt" $log_name $eidx
