#!/bin/bash
#SBATCH --job-name=decoding
#SBATCH --output=logs/slurm/decoding.%A.%a.out
#SBATCH --error=logs/slurm/decoding.%A.%a.err
#SBATCH --partition=shared-cpu
#SBATCH --array=1-3150:1
#SBATCH --mem-per-cpu=12000
#SBATCH --time=12:00:00

# extracting settings from $SLURM_ARRAY_TASK_ID
echo index $SLURM_ARRAY_TASK_ID

export PYTHONPATH="$PWD":$PYTHONPATH
# calling script

echo
~/mambaforge/envs/iblenv/bin/python pipelines/05_slurm_decode.py $SLURM_ARRAY_TASK_ID