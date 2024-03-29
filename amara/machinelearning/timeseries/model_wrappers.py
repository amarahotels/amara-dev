"""
This module provides functionality for model selection through exhaustive means or 
otherwise. Supports time series forecasting models provided by `statsmodels`. The name
`model_wrapper` may be misleading as this module provides wrappers for the object `type` 
and not its `instances`.
"""


from __future__ import annotations

import time
from typing import Callable, Iterable, Literal

import warnings; from statsmodels.tsa.base.tsa_model import ValueWarning
warnings.filterwarnings(action='ignore', category=UserWarning)
warnings.filterwarnings(action='ignore', category=ValueWarning)

import pandas as pd
import matplotlib.pyplot as plt
from statsmodels.tsa.arima.model import ARIMA, ARIMAResults
from sklearn.metrics import mean_absolute_percentage_error, mean_absolute_error, r2_score

from amara.visuals.progress import SingleProgressBar


class ARIMAWrapper:
    """
    Wrapper for the `ARIMA` class and its functionality provided by `statsmodels.tsa.arima.model`.
    """

    def __init__(self, train: pd.DataFrame, forecast: pd.DataFrame, target: str) -> None:
        """
        Creates an instance of `ARIMAWrapper`. Wraps the `ARIMA` class and provides
        extra functionality surrounding it. `train` and `forecast` must both have a 
        datetime index and exogenous variables but `forecast` does not need the target
        variable as a column.

        Parameters
        ----------
        `train` : `pd.DataFrame`
            Training data including exogenous variables.
        `forecast` : `pd.DataFrame`
            Forecast data including exogenous variables.
        `target` : `str`
            Target of the forecasting. 
        """
        
        self.__train = train
        self.__forecast = forecast

        self.__train_target = train[target]
        self.__train_exog = train.drop(target, axis=1)

        if target in self.__forecast:
            self.__forecast_target = forecast[target]
            self.__forecast_exog = forecast.drop(target, axis=1)
        else:
            self.__forecast_target = None
            self.__forecast_exog = forecast

    @property
    def target(self) -> pd.Series:
        """
        Returns the train and forecast targets as a concatenated `DataFrame` object or just the train
        targets if the target column does not exist in the forecast dataset.
        """

        if self.__forecast_target is not None:
            return pd.concat([self.__train_target, self.__forecast_target])
        return self.__train_target
    
    @property
    def forecast_length(self) -> int:
        """
        Returns the length of the forecast period in days
        """

        return len(self.__forecast)
    
    @property
    def forecast_exog(self) -> pd.DataFrame:
        """
        Returns the exogenous variables from the forecast dataset, every column that isn't the 
        target column
        """

        return self.__forecast_exog

    def exhaustive_search(self, p_values: list[int], d_values: list[int], q_values: list[int], metrics: list[Callable[[Iterable, Iterable], Iterable]], bounds: tuple[int, int] = None, return_models: bool = False) -> pd.DataFrame | tuple[pd.DataFrame, dict[tuple[int, int, int], ARIMAResults]]:
        """
        Exhaustively searches through the p, d and q value hyperparameters for the ARIMA 
        model and returns a DataFrame of passed models. Scores models based on their mean 
        absolute error (MAE), mean absolute percentage error (MAPE) and r2 score.

        Parameters
        ----------
        `p_values` : `list[int]`
            List of integers for `p`, the auto-regressive (AR) term.
        `d_values` : `list[int]`
            List of integers for `d`, the differencing count (I).
        `q_values` : `list[int]`
            List of integers for `q`, the moving average (MA) term.
        `metrics` : `list[Callable[[Iterable, Iterable], Iterable]]`
            List of metrics functions that take in 2 arguments, `y_true` and `y_pred` and returns 
            an iterable of the same length.
        `bounds` : `tuple[int, int]`, `default=None`
            Optional bounds for forecasted values. Values found not within these bounds will cause the 
            model to fail. Pass `None` for no bounds.
        `return_models` : `bool`, `default=False`
            Controls whether passed models are returned together with the results dataframe as a 
            tuple.

        Returns
        -------
        `pd.DataFrame`
            DataFrame of passed models and their metrics.
        `dict[tuple[int, int, int], ARIMAResults]`
            if `return_models` is `True`, returns trained models.
        """

        # init progress tracker
        steps_count = len(p_values) * len(d_values) * len(q_values)
        tracker = SingleProgressBar(steps_count, bar_length=100)
        passes, failures = 0, 0

        # passed models
        orders: list[list[str | float]] = []
        if return_models:
            models = {}

        # track time taken
        start = time.perf_counter()

        # exhaustive search over values
        for p in p_values:
            for d in d_values:
                for q in q_values:
                    
                    # in case of ARIMA fitting error
                    try:
                        # build model
                        model = ARIMA(self.__train_target, exog=self.__train_exog, order=(p, d, q), freq='D', enforce_invertibility=True, enforce_stationarity=True)
                        model_fit = model.fit(method='innovations_mle')

                        # get predictions
                        insample_pred = model_fit.predict()
                        outsample_fc = model_fit.get_forecast(len(self.__forecast), exog=self.__forecast_exog)
                        full_pred = pd.concat([insample_pred, outsample_fc.predicted_mean])
                        
                        if bounds is not None:
                            # check if values <0 or >100
                            if full_pred.apply(lambda x: True if x < bounds[0] or x > bounds[1] else False).any():
                                raise Exception
                        
                        # get model metrics based on train part
                        model_results = [(p, d, q)]
                        for metric in metrics:
                            model_results.append(metric(self.__train_target, insample_pred))

                        # return models if requested
                        if return_models:
                            models[(p, d, q)] = model_fit
                        
                        orders.append(model_results)
                        passes += 1

                    except Exception:
                        failures += 1

                    tracker.update()

        # print status report
        print(F'Passes: {passes} | Failures: {failures} | Time Taken: {time.perf_counter() - start:.2f}s')
        model_results = pd.DataFrame(orders).rename(columns={0: 'Order'} | {i: metric.__name__ for i, metric in enumerate(metrics, start=1)})

        if return_models:
            return model_results, models
        return model_results
    
    def forecast_with(self, model_fit: ARIMAResults, forecast: Literal['insample', 'outsample', 'full']) -> pd.Series:
        """
        Generates a forecast using a trained model `model_fit` passed with the option 
        to do an insample, outsample or full forecast.

        Parameters
        ----------
        `model_fit` : ARIMAResults
            Trained ARIMA model to generate a forecast
        `forecast` : Literal['insample', 'outsample', 'full']
            Option to decide the period the forecast is generated for

        Returns
        -------
        `pd.Series`
            A single-dimension iterable with the forecasted values
        """

        # get and return predictions/forecasts
        insample_pred = model_fit.predict()
        outsample_fc = model_fit.get_forecast(len(self.__forecast), exog=self.__forecast_exog)
        full_pred = pd.concat([insample_pred, outsample_fc.predicted_mean])

        if forecast == 'insample':
            return insample_pred
        if forecast == 'outsample':
            return outsample_fc
        if forecast == 'full':
            return full_pred
        return None

    def reconstruct(self, order: tuple[int, int, int], fit: bool = False) -> ARIMA | ARIMAResults:
        """
        Reconstructs an ARIMA model with the order passed and optionally fits it to 
        training data.

        Parameters
        ----------
        `order` : `tuple[int, int, int]`
            Order of the ARIMA model to be reconstructed
        `fit` : `bool`, `default=False`
            Whether to fit the model to the training data. If the model if fitted, 
            an ARIMAResults instance will be returned instad of an ARIMA instance.

        Returns
        -------
        `ARIMA | ARIMAResults`
            If the model if fitted, an ARIMAResults instance will be returned instad 
            of an ARIMA instance.
        """

        # build model
        model = ARIMA(self.__train_target, exog=self.__train_exog, order=order, freq='D', enforce_invertibility=True, enforce_stationarity=True)

        # bool to fit model or not
        if fit:
            model_fit = model.fit(method='innovations_mle')
            return model_fit
        return model
    
    @classmethod
    def parse_order(cls, order: str) -> tuple[int, int, int]:
        """
        Parses an ARIMA order from a string to a tuple of 3 integers.

        Parameters
        ----------
        `order` : `str`
            ARIMA model order as a string.
        
        Returns
        -------
        `tuple[int, int, int]`
            ARIMA model order as a tuple of 3 integers.
        """

        return order[1:-1].split(', ')