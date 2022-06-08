# Standard library
import logging
import pickle
from datetime import datetime as dt
from pathlib import Path
import yaml

# Third party libraries
import pandas as pd

# IBL libraries
from braindelphi.params import CACHE_PATH, SETTINGS_PATH
from braindelphi.pipelines.utils_common_pipelines import load_behavior
from braindelphi.pipelines.utils_common_pipelines import cache_behavior
from braindelphi.decoding.functions.utils import check_settings

# braindelphi repo imports
from braindelphi.utils_root import query_sessions

_logger = logging.getLogger('braindelphi')


def delayed_load(eid, target):
    try:
        return load_behavior(eid, target)
    except KeyError:
        pass


def delayed_save(subject, eid, target, outputs):
    return cache_behavior(subject, eid, target, outputs)


# load settings as a dict
settings = yaml.safe_load(open(SETTINGS_PATH))
kwargs = check_settings(settings)

dataset_futures = []

sessdf = query_sessions(kwargs['criterion']).set_index(['subject', 'eid'])

for eid in sessdf.index.unique(level='eid'):
    xsdf = sessdf.xs(eid, level='eid')
    subject = xsdf.index[0]
    load_outputs = delayed_load(eid, kwargs['target'])
    if not load_outputs['skip']:
        save_future = delayed_save(subject, eid, kwargs['target'], load_outputs)
        dataset_futures.append([subject, eid, save_future])

# Run below code AFTER futures have finished!
dataset = [{
    'subject': x[0],
    'eid': x[1],
    'meta_file': x[2][0],
    'reg_file': x[2][1]
} for i, x in enumerate(dataset_futures)]
dataset = pd.DataFrame(dataset)

outdict = {'params': kwargs, 'dataset_filenames': dataset}
outfile = Path(CACHE_PATH).joinpath(kwargs['date'] + '_%s_metadata.pkl' % kwargs['target'])
with open(outfile, 'wb') as fw:
    pickle.dump(outdict, fw)