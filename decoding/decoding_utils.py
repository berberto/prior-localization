import os
import numpy as np
import pandas as pd
import models.utils as mut
from pathlib import Path
from ibllib.atlas import BrainRegions
from iblutil.numerical import ismember
from one.api import ONE
from models.expSmoothing_prevAction import expSmoothing_prevAction
from models.expSmoothing_stimside import expSmoothing_stimside
from models.biasedApproxBayesian import biased_ApproxBayesian
from models.biasedBayesian import biased_Bayesian
from models.optimalBayesian import optimal_Bayesian

br = BrainRegions()

modeldispatcher = {expSmoothing_prevAction: 'expSmoothingPrevActions',
                   expSmoothing_stimside: 'expSmoothingStimSides',
                   biased_ApproxBayesian: 'biased_Approxbayesian',
                   biased_Bayesian: 'biased_Bayesian',
                   optimal_Bayesian: 'optimal_bayesian'
                   }

# Loading data and input utilities


def query_sessions(selection='all'):
    '''
    Filters sessions on some canonical filters
    returns dataframe with index being EID, so indexing results in subject name and probe
    identities in that EID.
    '''
    one = ONE()
    if selection == 'all':
        # Query all ephysChoiceWorld sessions
        ins = one.alyx.rest('insertions', 'list',
                            django='session__project__name__icontains,ibl_neuropixel_brainwide_01,'
                            'session__qc__lt,50')
    elif selection == 'aligned':
        # Query all sessions with at least one alignment
        ins = one.alyx.rest('insertions', 'list',
                            django='session__project__name__icontains,ibl_neuropixel_brainwide_01,'
                            'session__qc__lt,50,'
                            'json__extended_qc__alignment_count__gt,0')
    elif selection == 'resolved':
        # Query all sessions with resolved alignment
        ins = one.alyx.rest('insertions', 'list',
                            django='session__project__name__icontains,ibl_neuropixel_brainwide_01,'
                            'session__qc__lt,50,'
                            'json__extended_qc__alignment_resolved,True')
    elif selection == 'aligned-behavior':
        # Query sessions with at least one alignment and that meet behavior criterion
        ins = one.alyx.rest('insertions', 'list',
                            django='session__project__name__icontains,ibl_neuropixel_brainwide_01,'
                            'session__qc__lt,50,'
                            'json__extended_qc__alignment_count__gt,0,'
                            'session__extended_qc__behavior,1')
    elif selection == 'resolved-behavior':
        # Query sessions with resolved alignment and that meet behavior criterion
        ins = one.alyx.rest('insertions', 'list',
                            django='session__project__name__icontains,ibl_neuropixel_brainwide_01,'
                            'session__qc__lt,50,'
                            'json__extended_qc__alignment_resolved,True,'
                            'session__extended_qc__behavior,1')
    else:
        raise ValueError('Invalid selection was passed.'
                         'Must be in [\'all\', \'aligned\', \'resolved\', \'aligned-behavior\','
                         ' \'resolved-behavior\']')

    # Get list of eids and probes
    all_eids = np.array([i['session'] for i in ins])
    all_probes = np.array([i['name'] for i in ins])
    all_subjects = np.array([i['session_info']['subject'] for i in ins])
    retdf = pd.DataFrame({'subject': all_subjects, 'eid': all_eids, 'probe': all_probes})
    retdf.sort_values('subject', inplace=True)
    return retdf


def check_bhv_fit_exists(subject, model, eids, resultpath):
    '''
    subject: subject_name
    eids: sessions on which the model was fitted
    check if the behavioral fits exists
    return Bool and filename
    '''
    trainmeth = 'MCMC'  # This needs to be un-hard-coded if charles changes to diff. methods
    trunc_eids = [eid.split('-')[0] for eid in eids]
    str_sessionuuids = '_'.join(f'sess{k+1}_{eid}' for k, eid in enumerate(trunc_eids))
    if model not in modeldispatcher.keys():
        raise KeyError('Model is not an instance of a model from behavior_models')
    subjmodpath = Path(resultpath).joinpath(Path(subject))
    modstr = modeldispatcher[model]
    filen = f'model_{modstr}_train_{trainmeth}_train_' + str_sessionuuids + '.pkl'
    fullpath = subjmodpath.joinpath(filen)
    return os.path.exists(fullpath), fullpath


def fit_load_bhvmod(target, subject, savepath, eids_train, eid_test, remove_old=False,
                    modeltype=expSmoothing_prevAction, one=None):
    '''
    load/fit a behavioral model to compute target on a single session
    Params:
        eids_train: list of eids on which we train the network
        eid_test: eid on which we want to compute the target signals, only one string
    '''
    one = one or ONE()

    # check if is trained
    istrained, fullpath = check_bhv_fit_exists(subject, modeltype, eids_train, savepath)

    if not istrained:
        datadict = {'stim_side': [], 'actions': [], 'stimuli': []}
        for eid in eids_train:
            data = mut.load_session(eid, one=one)
            if data['choice'] is None:
                raise ValueError('Session choices produced are None.'
                                 'Debug models.utils.load_session,'
                                 f' or remove the eid {eid} from your input list.')
            stim_side, stimuli, actions, _ = mut.format_data(data)
            datadict['stim_side'].append(stim_side)
            datadict['stimuli'].append(stimuli)
            datadict['actions'].append(actions)
        stimuli, actions, stim_side = mut.format_input(datadict['stimuli'], datadict['actions'],
                                                       datadict['stim_side'])
        eids = np.array(eids_train)
        model = modeltype(savepath, eids, subject,
                          actions, stimuli, stim_side)
        model.load_or_train(remove_old=remove_old)
    else:
        model = modeltype(savepath, eids_train, subject, actions=None, stimuli=None,
                          stim_side=None)
        model.load_or_train(loadpath=fullpath)

    # load test session
    data = mut.load_session(eid_test, one=one)
    stim_side, stimuli, actions, _ = mut.format_data(data)
    stimuli, actions, stim_side = mut.format_input([stimuli], [actions], [stim_side])

    # compute signal
    signal = model.compute_signal(signal=target, act=actions, stim=stimuli, side=stim_side)[target]

    return signal.squeeze()


def remap_region(ids, source='Allen-lr', dest='Beryl-lr', output='acronym'):
    _, inds = ismember(ids, br.id[br.mappings[source]])
    ids = br.id[br.mappings[dest][inds]]
    if output == 'id':
        return br.id[br.mappings[dest][inds]]
    elif output == 'acronym':
        return br.get(br.id[br.mappings[dest][inds]])['acronym']
    elif output == 'name':
        return br.get(br.id[br.mappings[dest][inds]])['name']
    else:
        return br.get(br.id[br.mappings[dest][inds]])


def compute_target(target, subject, eids_train, eid_test, savepath,
                   modeltype=expSmoothing_prevAction, pseudo=False, one=None):
    """
    Computes regression target for use with regress_target, using subject, eid, and a string
    identifying the target parameter to output a vector of N_trials length containing the target

    Parameters
    ----------
    target : str
        String in ['prior', 'prederr', 'signcont'], indication model-based prior, prediction error,
        or simple signed contrast per trial
    subject : str
        Subject identity in the IBL database, e.g. KS022
    eids_train : list of str
        list of UUID identifying sessions on which the model is trained.
    eids_test : str
        UUID identifying sessions on which the target signal is computed
    savepath : str
        where the beh model outputs are saved
    behmodel : str
        behmodel to use
    pseudo : bool
        Whether or not to compute a pseudosession result, rather than a real result.
    modeltype : behavior_models model object
        Instantiated object of behavior models. Needs to be instantiated for pseudosession target
        generation in the case of a 'prior' or 'prederr' target.

    Returns
    -------
    pandas.Series
        Pandas series in which index is trial number, and value is the target
    """
    possible_targets = ['prior', 'prederr', 'signcont']
    if target not in possible_targets:
        raise ValueError('target should be in {}'.format(possible_targets))

    target = fit_load_bhvmod(target, subject, savepath, eids_train, eid_test, remove_old=False,
                             modeltype=modeltype, one=one)
    return target


def regress_target(tvec, binned, estimator,
                   hyperparam_grid=None, test_prop=0.2, interleave_test=True, grid_cv=None):
    """
    Regresses binned neural activity against a target, using a provided sklearn estimator

    Parameters
    ----------
    tvec : pandas.Series
        Series in which trial number is the index and the value is the regression target
    binned : numpy.ndarray
        N_trials X N_neurons array, in which a single element is a spike count on a trial for a
        given neuron
    estimator : sklearn.linear_model estimator
        Estimator from sklearn which provides .fit, .score, and .predict methods. CV estimators
        are NOT SUPPORTED. Must be a normal estimator, which is internally wrapped with
        GridSearchCV
    hyperparam_grid : dict
        Dictionary with key indicating hyperparameter to grid search over, and value being a the
        nodes on the grid. See sklearn.model_selection.GridSearchCV : param_grid for more specs.
        Defaults to None, which means no hyperparameter estimation or GridSearchCV use.
    test_prop : float in (0, 1)
        Proportion of data to hold out for testing after fitting (with or without grid search)
    interleave_test : bool
        Whether or not test trials should be randomly selected from among the experiment. False
        means last 20% of the experiment will be used.
    grid_cv : See sklearn.model_selection.GridSearchCV, cv argument
        passed through to determine how hyperparameter estimation is done.

    Returns
    -------
    dict
        Dictionary of fitting outputs including:
            - Regression score (from estimator)
            - Decoding coefficients
            - Decoding intercept
            - Per-trial target values (copy of tvec)
            - Per-trial predictions from model
    """
    ## Do some stuff
    outdict = dict()

    # fit estimator to output
    estimator.fit(binned, tvec)

    outdict['score'] = estimator.score(binned, tvec)
    outdict['weights'] = estimator.coef_
    outdict['intercept'] = estimator.intercept_
    outdict['target'] = tvec
    outdict['prediction'] = estimator.predict(binned)

    return outdict


if __name__ == '__main__':

    one = ONE()
    mice_names, ins, ins_id, sess_id, _ = mut.get_bwm_ins_alyx(one)
    stimuli_arr, actions_arr, stim_sides_arr, session_uuids = [], [], [], []

    # select particular mice
    mouse_name = 'KS016'
    for i in range(len(sess_id)):
        if mice_names[i] == mouse_name:  # take only sessions of first mice
            data = mut.load_session(sess_id[i])
            if data['choice'] is not None and data['probabilityLeft'][0] == 0.5:
                stim_side, stimuli, actions, pLeft_oracle = mut.format_data(data)
                stimuli_arr.append(stimuli)
                actions_arr.append(actions)
                stim_sides_arr.append(stim_side)
                session_uuids.append(sess_id[i])

    # format data
    stimuli, actions, stim_side = mut.format_input(stimuli_arr, actions_arr, stim_sides_arr)
    session_uuids = np.array(session_uuids)

    # launch inference
    # model = exp_prevAction('./results/inference/', session_uuids, mouse_name, actions, stimuli,
    #                        stim_side)
    # model.load_or_train(remove_old=False)
    # param = model.get_parameters()  # if you want the parameters
    # signals = model.compute_signal(signal=['prior', 'prediction_error', 'score'],
    #                               verbose=False)  # compute signals of interest

    # debug
    subject = mouse_name

    target = compute_target('prior', subject, session_uuids[:1], session_uuids[0], 'results/inference/',
                   modeltype=expSmoothing_prevAction)