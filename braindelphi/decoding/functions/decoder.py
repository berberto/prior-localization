import numpy as np
import pandas as pd
from sklearn import linear_model as sklm
from sklearn.metrics import accuracy_score, balanced_accuracy_score, r2_score
from sklearn.model_selection import GridSearchCV, KFold, train_test_split

from braindelphi.decoding.functions.balancedweightings import balanced_weighting


def decode_cv(
        ys,
        Xs,
        estimator,
        estimator_kwargs,
        use_openturns,
        target_distribution,
        bin_size_kde,
        balanced_continuous_target=True,
        balanced_weight=False,
        hyperparam_grid=None,
        test_prop=0.2,
        n_folds=5,
        save_binned=False,
        save_predictions=True,
        verbose=False,
        shuffle=True,
        outer_cv=True,
        rng_seed=0,
        normalize_input=False,
        normalize_output=False
):
    """Regresses binned neural activity against a target, using a provided sklearn estimator.

    Parameters
    ----------
    ys : list of arrays or np.ndarray or pandas.Series
        targets; if list, each entry is an array of targets for one trial. if 1D numpy array, each
        entry is treated as a single scalar for one trial. if pd.Series, trial number is the index
        and teh value is the target.
    Xs : list of arrays or np.ndarray
        predictors; if list, each entry is an array of neural activity for one trial. if 2D numpy
        array, each row is treated as a single vector of ativity for one trial, i.e. the array is
        of shape (n_trials, n_neurons)
    estimator : sklearn.linear_model object
        estimator from sklearn which provides .fit, .score, and .predict methods. CV estimators
        are NOT SUPPORTED. Must be a normal estimator, which is internally wrapped with
        GridSearchCV
    estimator_kwargs : dict
        additional arguments for sklearn estimator
    use_openturns : bool
    target_distribution : ?
        ?
    bin_size_kde : float
        ?
    balanced_weight : ?
        ?
    balanced_continuous_target : ?
        ?
    hyperparam_grid : dict
        key indicates hyperparameter to grid search over, and value is an array of nodes on the
        grid. See sklearn.model_selection.GridSearchCV : param_grid for more specs.
        Defaults to None, which means no hyperparameter estimation or GridSearchCV use.
    test_prop : float
        proportion of data to hold out as the test set after running hyperparameter tuning; only
        used if `outer_cv=False`
    n_folds : int
        Number of folds for cross-validation during hyperparameter tuning; only used if
        `outer_cv=True`
    save_binned : bool
        True to put the regressors Xs into the output dictionary.
        Can cause file bloat if saving outputs.
        Note: this function does not actually save any files!
    save_predictions : bool
        True to put the model predictions into the output dictionary.
        Can cause file bloat if saving outputs.
        Note: this function does not actually save any files!
    shuffle : bool
        True for interleaved cross-validation, False for contiguous blocks
    outer_cv: bool
        Perform outer cross validation such that the testing spans the entire dataset
    rng_seed : int
        control data splits
    verbose : bool
        Whether you want to hear about the function's life, how things are going, and what the
        neighbor down the street said to it the other day.
    normalize_output : bool
        True to take out the mean across trials of the output
    normalize_input : bool
        True to take out the mean across trials of the input; average is taken across trials for
        each unit (one average per unit is computed)

    Returns
    -------
    dict
        Dictionary of fitting outputs including:
            - Regression score (from estimator)
            - Decoding coefficients
            - Decoding intercept
            - Per-trial target values (copy of tvec)
            - Per-trial predictions from model
            - Input regressors (optional, see Xs argument)

    """

    # transform target data into standard format: list of floats
    if isinstance(ys, np.ndarray):
        ys = [np.array([y]) for y in ys]
    elif isinstance(ys, pd.Series):
        ys = ys.to_numpy()
        ys = [np.array([y]) for y in ys]

    # transform neural data into standard format: list of np.ndarrays
    if isinstance(Xs, np.ndarray):
        Xs = [x[None, :] for x in Xs]

    # initialize containers to save outputs
    n_trials = len(Xs)
    bins_per_trial = len(Xs[0])
    scores_test, scores_train = [], []
    idxes_test, idxes_train = [], []
    weights, intercepts, best_params = [], [], []
    predictions = [None for _ in range(n_trials)]
    predictions_to_save = [None for _ in range(n_trials)]  # different for logistic regression

    # split the dataset in two parts, train and test
    # when shuffle=False, the method will take the end of the dataset to create the test set
    np.random.seed(rng_seed)
    indices = np.arange(n_trials)
    if outer_cv:
        outer_kfold = KFold(n_splits=n_folds, shuffle=shuffle).split(indices)
    else:
        outer_kfold = iter([train_test_split(indices, test_size=test_prop, shuffle=shuffle)])

    # scoring function; use R2 for linear regression, accuracy for logistic regression
    scoring_f = balanced_accuracy_score if (estimator == sklm.LogisticRegression) else r2_score

    # Select either the GridSearchCV estimator for a normal estimator, or use the native estimator
    # in the case of CV-type estimators
    if isinstance(estimator, sklm.LinearModelCV):
        raise NotImplemented('the code does not support a CV-type estimator for the moment.')
    else:
        # loop over outer folds
        for train_idxs_outer, test_idxs_outer in outer_kfold:

            # outer fold data split
            # X_train = np.vstack([Xs[i] for i in train_idxs])
            # y_train = np.concatenate([ys[i] for i in train_idxs], axis=0)
            # X_test = np.vstack([Xs[i] for i in test_idxs])
            # y_test = np.concatenate([ys[i] for i in test_idxs], axis=0)
            X_train = [Xs[i] for i in train_idxs_outer]
            y_train = [ys[i] for i in train_idxs_outer]
            X_test = [Xs[i] for i in test_idxs_outer]
            y_test = [ys[i] for i in test_idxs_outer]

            # now loop over inner folds
            idx_inner = np.arange(len(X_train))
            inner_kfold = KFold(n_splits=n_folds, shuffle=shuffle).split(idx_inner)

            key = list(hyperparam_grid.keys())[0]  # TODO: make this more robust
            r2s = np.zeros([n_folds, len(hyperparam_grid[key])])
            for ifold, (train_idxs_inner, test_idxs_inner) in enumerate(inner_kfold):

                # inner fold data split
                X_train_inner = np.vstack([X_train[i] for i in train_idxs_inner])
                y_train_inner = np.concatenate([y_train[i] for i in train_idxs_inner], axis=0)
                X_test_inner = np.vstack([X_train[i] for i in test_idxs_inner])
                y_test_inner = np.concatenate([y_train[i] for i in test_idxs_inner], axis=0)

                # normalize inputs/outputs if requested
                mean_X_train_inner = X_train_inner.mean(axis=0) if normalize_input else 0
                X_train_inner = X_train_inner - mean_X_train_inner
                X_test_inner = X_test_inner - mean_X_train_inner
                mean_y_train_inner = y_train_inner.mean(axis=0) if normalize_output else 0
                y_train_inner = y_train_inner - mean_y_train_inner

                for i_alpha, alpha in enumerate(hyperparam_grid[key]):

                    # compute weight for each training sample if requested
                    # (esp necessary for classification problems with imbalanced classes)
                    if balanced_weight:
                        sample_weight = balanced_weighting(
                            vec=y_train_inner,
                            continuous=balanced_continuous_target,
                            use_openturns=use_openturns,
                            bin_size_kde=bin_size_kde,
                            target_distribution=target_distribution)
                    else:
                        sample_weight = None

                    # initialize model
                    model_inner = estimator(**{**estimator_kwargs, key: alpha})
                    # fit model
                    model_inner.fit(X_train_inner, y_train_inner, sample_weight=sample_weight)
                    # evaluate model
                    pred_test_inner = model_inner.predict(X_test_inner) + mean_y_train_inner
                    r2s[ifold, i_alpha] = scoring_f(y_test_inner, pred_test_inner)

            # select model with best hyperparameter value evaluated on inner-fold test data;
            # refit/evaluate on all inner-fold data
            r2s_avg = r2s.mean(axis=0)

            # normalize inputs/outputs if requested
            X_train_array = np.vstack(X_train)
            mean_X_train = X_train_array.mean(axis=0) if normalize_input else 0
            X_train_array = X_train_array - mean_X_train

            y_train_array = np.concatenate(y_train, axis=0)
            mean_y_train = y_train_array.mean(axis=0) if normalize_output else 0
            y_train_array = y_train_array - mean_y_train

            # compute weight for each training sample if requested
            if balanced_weight:
                sample_weight = balanced_weighting(
                    vec=y_train_array,
                    continuous=balanced_continuous_target,
                    use_openturns=use_openturns,
                    bin_size_kde=bin_size_kde,
                    target_distribution=target_distribution)
            else:
                sample_weight = None

            # initialize model
            best_alpha = hyperparam_grid[key][np.argmax(r2s_avg)]
            model = estimator(**{**estimator_kwargs, key: best_alpha})
            # fit model
            model.fit(X_train_array, y_train_array, sample_weight=sample_weight)

            # evalute model on train data
            y_pred_train = model.predict(X_train_array) + mean_y_train
            scores_train.append(
                scoring_f(y_train_array + mean_y_train, y_pred_train + mean_y_train))

            # evaluate model on test data
            y_true = np.concatenate(y_test, axis=0)
            y_pred = model.predict(np.vstack(X_test) - mean_X_train) + mean_y_train
            if isinstance(estimator, sklm.LogisticRegression) and bins_per_trial == 1:
                y_pred_probs = model.predict_proba(
                    np.vstack(X_test) - mean_X_train)[:, 0] + mean_y_train
            else:
                y_pred_probs = None
            scores_test.append(scoring_f(y_true, y_pred))

            # save the raw prediction in the case of linear and the predicted probabilities when
            # working with logitistic regression
            for i_fold, i_global in enumerate(test_idxs_outer):
                if bins_per_trial == 1:
                    # we already computed these estimates, take from above
                    predictions[i_global] = y_pred[i_fold]
                    if isinstance(estimator, sklm.LogisticRegression):
                        predictions_to_save[i_global] = y_pred_probs[i_fold]
                    else:
                        predictions_to_save[i_global] = predictions[i_global]
                else:
                    # we already computed these above, but after all trials were stacked; recompute
                    # per-trial
                    predictions[i_global] = model.predict(
                        X_test[i_fold] - mean_X_train) + mean_y_train
                    if isinstance(estimator, sklm.LogisticRegression):
                        predictions_to_save[i_global] = model.predict_proba(
                            X_test[i_fold] - mean_X_train)[:, 0] + mean_y_train
                    else:
                        predictions_to_save[i_global] = predictions[i_global]

            # save out other data of interest
            idxes_test.append(test_idxs_outer)
            idxes_train.append(train_idxs_outer)
            weights.append(model.coef_)
            if model.fit_intercept:
                intercepts.append(model.intercept_)
            else:
                intercepts.append(None)
            best_params.append({key: best_alpha})

    ys_true_full = np.concatenate(ys, axis=0)
    ys_pred_full = np.concatenate(predictions, axis=0)
    outdict = dict()
    outdict['scores_test_full'] = scoring_f(ys_true_full, ys_pred_full)
    outdict['scores_train'] = scores_train
    outdict['scores_test'] = scores_test
    outdict['Rsquared_test_full'] = r2_score(ys_true_full, ys_pred_full)
    if estimator == sklm.LogisticRegression:
        outdict['acc_test_full'] = accuracy_score(ys_true_full, ys_pred_full)
        outdict['balanced_acc_test_full'] = balanced_accuracy_score(ys_true_full, ys_pred_full)
    outdict['weights'] = weights
    outdict['intercepts'] = intercepts
    outdict['target'] = ys
    outdict['predictions_test'] = predictions_to_save if save_predictions else None
    outdict['regressors'] = Xs if save_binned else None
    outdict['idxes_test'] = idxes_test
    outdict['idxes_train'] = idxes_train
    outdict['best_params'] = best_params
    outdict['n_folds'] = n_folds
    if hasattr(model, 'classes_'):
        outdict['classes_'] = model.classes_

    # logging
    if verbose:
        # verbose output
        if outer_cv:
            print('Performance is only described for last outer fold \n')
        print("Possible regularization parameters over {} validation sets:".format(n_folds))
        print('{}: {}'.format(list(hyperparam_grid.keys())[0], hyperparam_grid))
        print("\nBest parameters found over {} validation sets:".format(n_folds))
        print(model.best_params_)
        print("\nAverage scores over {} validation sets:".format(n_folds))
        means = model.cv_results_["mean_test_score"]
        stds = model.cv_results_["std_test_score"]
        for mean, std, params in zip(means, stds, model.cv_results_["params"]):
            print("%0.3f (+/-%0.03f) for %r" % (mean, std * 2, params))
        print("\n", "Detailed scores on {} validation sets:".format(n_folds))
        for i_fold in range(n_folds):
            tscore_fold = list(
                np.round(model.cv_results_['split{}_test_score'.format(int(i_fold))], 3))
            print("perf on fold {}: {}".format(int(i_fold), tscore_fold))

        print("\n", "Detailed classification report:", "\n")
        print("The model is trained on the full (train + validation) set.")

    return outdict