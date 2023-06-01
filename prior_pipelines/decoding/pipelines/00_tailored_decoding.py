import pandas as pd
import sys
from prior_pipelines.decoding.settings import kwargs, N_PSEUDO_PER_JOB, N_PSEUDO
from prior_pipelines.decoding.functions.decoding import fit_eid
import numpy as np
from prior_pipelines.params import CACHE_PATH
from prior_pipelines.pipelines.utils_ephys import load_ephys
from one.api import ONE
from brainwidemap import bwm_query

# parameters
try:
    i_eid = int(sys.argv[1])
except:
    i_eid = 1

MERGE_PROBES = True # test is defined for MERGE_PROBES True or False
ALGN_RESOLVED = True
QC = 1

kwargs['set_seed_for_DEBUG'] = True
kwargs['run_integration_test'] = True # if you put False, you will overwrite the existing "groundtruth"

# ONE
one = ONE()
one.alyx.clear_rest_cache()
bwm_df = bwm_query().set_index(["subject", "eid"])  # freeze="2022_10_update"

# get all eids
eids = bwm_df.index.unique(level="eid")

# select session, eid, and pids and subject of interest
session_df = bwm_df.xs(eids[i_eid], level="eid")
subject = session_df.index[0]
pids = session_df.pid.to_list()
probe_names = session_df.probe_name.to_list()
eid = eids[i_eid]
metadata = {
    "subject": subject,
    "eid": eid,
    "probe_name": 'merged_probes' if MERGE_PROBES else None,
    "ret_qc": QC,
}

# decoding function
def decode(regressors, metadata, nb_pseudo_sessions=3):
    if kwargs['neural_dtype'] == 'widefield':
        trials_df, neural_dict = regressors
    else:
        trials_df, neural_dict = regressors['trials_df'], regressors

    # pseudo_id=-1 decodes the prior of the session, pseudo_id>0 decodes pseudo-priors
    pseudo_ids = np.concatenate((-np.ones(1), np.arange(1, nb_pseudo_sessions))).astype('int64')
    results_fit_eid = fit_eid(neural_dict=neural_dict, trials_df=trials_df, metadata=metadata,
                            pseudo_ids=pseudo_ids, **kwargs)
    return results_fit_eid # these are filenames

# launch decoding
results_fit_eid = []
if MERGE_PROBES:
    regressors = load_ephys(eid, pids, one=one, **{'ret_qc':True})
    results_fit_eid.extend(decode(regressors, metadata))
else:
    probe_names = session_df.probe_name.to_list()
    for (pid, probe_name) in zip(pids, probe_names):
        regressors =  load_ephys(eids[i_eid], [pid], one=one, ret_qc=QC)
        metadata['probe_name'] = probe_name
        results_fit_eid.extend(decode(regressors, metadata))

if kwargs['run_integration_test']:
    import pickle
    for f in results_fit_eid:        
        predicteds = pickle.load(open(f, 'rb'))
        targets = pickle.load(open(f.parent.joinpath(f.name.replace('_to_be_tested', '')), 'rb'))
        for (fit_pred, fit_targ) in zip(predicteds['fit'], targets['fit']):
            assert (fit_pred['Rsquared_test_full'] == fit_targ['Rsquared_test_full'])
            assert (fit_pred['predictions_test'] == fit_targ['predictions_test'])
        f.unlink() # remove predicted path

print('job successful')

