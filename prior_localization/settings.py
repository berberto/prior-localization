import logging
import numpy as np
from prior_localization.functions.process_targets import optimal_Bayesian
from behavior_models.models import ActionKernel, StimulusKernel
from prior_localization.params import FIT_PATH as NEURAL_MOD_PATH
from prior_localization.params import BEH_MOD_PATH as BEHAVIOR_MOD_PATH
import sklearn.linear_model as sklm

NEURAL_MOD_PATH.mkdir(parents=True, exist_ok=True)
BEHAVIOR_MOD_PATH.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("ibllib")
logger.disabled = True

NEURAL_DTYPE = "ephys"  # "widefield"  # 'ephys' or 'widefield'
DATE = "30-01-2023"  # date 12 prev, 13 next, 14 prev

modeldispatcher = {
    ActionKernel: ActionKernel.name,
    StimulusKernel: StimulusKernel.name,
    optimal_Bayesian: "optBay",
    None: "oracle",
}




# aligned -> histology was performed by one experimenter
# resolved -> histology was performed by 2-3 experiments
SESS_CRITERION = "resolved-behavior"  # aligned and behavior
ALIGN_TIME = "stimOn_times"
TARGET = "pLeft"  # 'signcont' or 'pLeft'
if TARGET not in ["pLeft", "signcont", "strengthcont", "choice", "feedback"]:
    raise ValueError(
        "TARGET can only be pLeft, signcont, strengthcont, choice or feedback"
    )
# NB: if TARGET='signcont', MODEL with define how the neurometric curves will be generated. else MODEL computes TARGET
# if MODEL is a path, this will be the interindividual results
MODEL = optimal_Bayesian #StimulusKernel, ActionKernel, optimal_Bayesian or None(=Oracle)
TIME_WINDOW = (-0.6, -0.1)  # (0, 0.1)  # only ephys
ESTIMATOR = sklm.Ridge # Must be in keys of strlut above
USE_NATIVE_SKLEARN_FOR_HYPERPARAMETER_ESTIMATION = (ESTIMATOR == sklm.Ridge)
BINARIZATION_VALUE = (
    None  # to binarize the target -> could be useful with logistic regression estimator
)
ESTIMATOR_KWARGS = {"tol": 0.0001, "max_iter": 20000, "fit_intercept": True}
N_PSEUDO = 200
N_PSEUDO_PER_JOB = 10
N_JOBS_PER_SESSION = N_PSEUDO // N_PSEUDO_PER_JOB
N_RUNS = 2
MIN_UNITS = 10
NB_TRIALS_TAKEOUT_END = 0
MIN_BEHAV_TRIALS = (
    150 if NEURAL_DTYPE == "ephys" else 150
)  # default BWM setting is 400. 200 must remain after filtering

MERGED_PROBES = False # merge probes before performing analysis
SHUFFLE = True  # interleaved cross validation
BORDER_QUANTILES_NEUROMETRIC = [0.3, 0.7]  # [.3, .4, .5, .6, .7]
COMPUTE_NEUROMETRIC = False
SAVE_PREDICTIONS = True

# Basically, quality metric on the stability of a single unit. Should have 1 metric per neuron
QC_CRITERIA = 3 / 3  # 3 / 3  # In {None, 1/3, 2/3, 3/3}
QUASI_RANDOM = False  # if TRUE, decoding is launched in a quasi-random, reproducible way => it sets the seed

BALANCED_WEIGHT = True  # seems to work better with BALANCED_WEIGHT=False, but putting True is important
HPARAM_GRID = (
    {
        #'alpha': np.array([0.001, 0.01, 0.1, 1, 10, 100, 1000, 10000, 100000])
        "alpha": np.array(
           [
                0.00001,
                0.0001,
                0.001,
                0.01,
                0.1, 
            ]
        )  # lasso , 0.01, 0.1
    }
    if not (sklm.LogisticRegression == ESTIMATOR)
    else {"C": np.array([0.00001, 0.0001, 0.001, 0.01, 0.1, 1, 10])}
)
DEBUG = False  # in debugging/unit testing mode
SAVE_BINNED = False  # Debugging parameter, not usually necessary
COMPUTE_NEURO_ON_EACH_FOLD = False  # if True, expect a script that is 5 times slower

# WIDE FIELD IMAGING
WFI_HEMISPHERES = ["left", "right"]  # 'left' and/or 'right'
WFI_NB_FRAMES_START = (
    -2
)  # left signed number of frames from ALIGN_TIME (frame included)
WFI_NB_FRAMES_END = (
    -2
)  # right signed number of frames from ALIGN_TIME (frame included). If 0, the align time frame is included
WFI_AVERAGE_OVER_FRAMES = False

if NEURAL_DTYPE == "widefield" and WFI_NB_FRAMES_START > WFI_NB_FRAMES_END:
    raise ValueError(
        "there is a problem in the specification of the timing of the widefield"
    )


# DEEPLABCUT MOVEMENT REGRESSORS
MOTOR_REGRESSORS = False
MOTOR_REGRESSORS_ONLY = False  # only _use motor regressors

# DO WE WANT TO DECODE MOTOR RESIDUAL OF THE PRIOR TARGET (WORK ONLY FOR OPTI BAYES)
MOTOR_RESIDUAL = False

# DO WE WANT TO DECODE THE DERIVATIVE OF THE TARGET SIGNAL ?
DECODE_DERIVATIVE = False


ADD_TO_PATH = {'balanced_weighting': BALANCED_WEIGHT}

# session to be excluded (by Olivier Winter)
excludes = [
    "bb6a5aae-2431-401d-8f6a-9fdd6de655a9",  # inconsistent trials object: relaunched task on 31-12-2021
    "c7b0e1a3-4d4d-4a76-9339-e73d0ed5425b",  # same same
    "7a887357-850a-4378-bd2a-b5bc8bdd3aac",  # same same
    "56b57c38-2699-4091-90a8-aba35103155e",  # load object pickle error
    "09394481-8dd2-4d5c-9327-f2753ede92d7",  # same same
]

strlut = {
    sklm.Lasso: "Lasso",
    sklm.LassoCV: "LassoCV",
    sklm.Ridge: "Ridge",
    sklm.RidgeCV: "RidgeCV",
    sklm.LinearRegression: "PureLinear",
    sklm.LogisticRegression: "Logistic",
}

if TARGET in ["choice", "feedback"] and (
    MODEL != ActionKernel
):
    raise ValueError(
        "if you want to decode choice or feedback, you must use the actionKernel model and frankenstein sessions"
    )

# ValueErrors and NotImplementedErrors
if MODEL not in list(modeldispatcher.keys()) and not isinstance(MODEL, str):
    raise NotImplementedError("this MODEL is not supported yet")

if COMPUTE_NEUROMETRIC and TARGET != "signcont":
    raise ValueError("the target should be signcont to compute neurometric curves")

if len(BORDER_QUANTILES_NEUROMETRIC) == 0 and MODEL is not None:
    raise ValueError(
        "BORDER_QUANTILES_NEUROMETRIC must be at least of 1 when MODEL is specified"
    )

if len(BORDER_QUANTILES_NEUROMETRIC) != 0 and MODEL is None:
    raise ValueError(
        "BORDER_QUANTILES_NEUROMETRIC must be empty when MODEL is not specified - oracle pLeft used"
    )

fit_metadata = {
    "date": DATE,
    "criterion": SESS_CRITERION,
    "target": TARGET,
    "model_type": modeldispatcher[MODEL],
    "align_time": ALIGN_TIME,
    "time_window": TIME_WINDOW,
    "estimator": ESTIMATOR,
    "nb_runs": N_RUNS,
    "n_pseudo": N_PSEUDO,
    "min_units": MIN_UNITS,
    "min_behav_trials": MIN_BEHAV_TRIALS,
    "qc_criteria": QC_CRITERIA,
    "shuffle": SHUFFLE,
    "hyperparameter_grid": HPARAM_GRID,
    "save_binned": SAVE_BINNED,
    "balanced_weight": BALANCED_WEIGHT,
    "compute_neurometric": COMPUTE_NEUROMETRIC,
    "n_runs": N_RUNS,
    "neural_dtype": NEURAL_DTYPE,
    "modeldispatcher": modeldispatcher,
    "behfit_path": BEHAVIOR_MOD_PATH,
    "neuralfit_path": NEURAL_MOD_PATH,
    "estimator_kwargs": ESTIMATOR_KWARGS,
    "hyperparam_grid": HPARAM_GRID,
    "add_to_saving_path": ADD_TO_PATH,
    "save_predictions": SAVE_PREDICTIONS,
    "wfi_nb_frames_start": WFI_NB_FRAMES_START,
    "wfi_nb_frames_end": WFI_NB_FRAMES_END,
    "quasi_random": QUASI_RANDOM,
    "motor_regressors": MOTOR_REGRESSORS,
    "motor_regressors_only": MOTOR_REGRESSORS_ONLY,
    "decode_derivative": DECODE_DERIVATIVE,
    "motor_residual": MOTOR_RESIDUAL,
    "use_native_sklearn_for_hyperparameter_estimation": USE_NATIVE_SKLEARN_FOR_HYPERPARAMETER_ESTIMATION,
    "set_seed_for_DEBUG": False
}

if NEURAL_DTYPE == "widefield":
    fit_metadata["wfi_hemispheres"] = WFI_HEMISPHERES
    fit_metadata["wfi_nb_frames"] = WFI_HEMISPHERES

kwargs = {
    "date": DATE,
    "nb_runs": N_RUNS,
    "merged_probes": MERGED_PROBES,
    "neuralfit_path": NEURAL_MOD_PATH,
    "behfit_path": BEHAVIOR_MOD_PATH,
    "modeldispatcher": modeldispatcher,
    "estimator_kwargs": ESTIMATOR_KWARGS,
    "hyperparam_grid": HPARAM_GRID,
    "save_binned": SAVE_BINNED,
    "shuffle": SHUFFLE,
    "balanced_weight": BALANCED_WEIGHT,
    "compute_on_each_fold": COMPUTE_NEURO_ON_EACH_FOLD,
    "estimator": ESTIMATOR,
    "target": TARGET,
    "model": MODEL,
    "align_time": ALIGN_TIME,
    "min_behav_trials": MIN_BEHAV_TRIALS,
    "qc_criteria": QC_CRITERIA,
    "min_units": MIN_UNITS,
    "time_window": TIME_WINDOW,
    "compute_neurometric": COMPUTE_NEUROMETRIC,
    "border_quantiles_neurometric": BORDER_QUANTILES_NEUROMETRIC,
    "add_to_saving_path": ADD_TO_PATH,
    "neural_dtype": NEURAL_DTYPE,
    "wfi_hemispheres": WFI_HEMISPHERES,
    "binarization_value": BINARIZATION_VALUE,
    "save_predictions": SAVE_PREDICTIONS,
    "wfi_nb_frames_start": WFI_NB_FRAMES_START,
    "wfi_nb_frames_end": WFI_NB_FRAMES_END,
    "quasi_random": QUASI_RANDOM,
    "nb_trials_takeout_end": NB_TRIALS_TAKEOUT_END,
    "motor_regressors": MOTOR_REGRESSORS,
    "motor_regressors_only": MOTOR_REGRESSORS_ONLY,
    "decode_derivative": DECODE_DERIVATIVE,
    "motor_residual": MOTOR_RESIDUAL,
    "wfi_average_over_frames": WFI_AVERAGE_OVER_FRAMES,
    "debug": DEBUG,
    "use_native_sklearn_for_hyperparameter_estimation": USE_NATIVE_SKLEARN_FOR_HYPERPARAMETER_ESTIMATION,
    "set_seed_for_DEBUG": False
}


region_defaults = {
    'widefield': [
            ["ACAd"],
            ["AUDd"],
            ["AUDp"],
            ["AUDpo"],
            ["AUDv"],
            ["FRP"],
            ["MOB"],
            ["MOp"],
            ["MOs"],
            ["PL"],
            ["RSPagl"],
            ["RSPd"],
            ["RSPv"],
            ["SSp-bfd"],
            ["SSp-ll"],
            ["SSp-m"],
            ["SSp-n"],
            ["SSp-tr"],
            ["SSp-ul"],
            ["SSp-un"],
            ["SSs"],
            ["TEa"],
            ["VISa"],
            ["VISal"],
            ["VISam"],
            ["VISl"],
            ["VISli"],
            ["VISp"],
            ["VISpl"],
            ["VISpm"],
            ["VISpor"],
            ["VISrl"],
        ]
}

