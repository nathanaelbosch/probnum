import numpy as np

import probnum.filtsmooth as pnfs
from probnum.random_variables import Normal

from ..odesolver import ODESolver
from .kalman_odesolution import KalmanODESolution


class GaussianIVPFilter(ODESolver):
    """ODE solver that uses a Gaussian filter.

    This is based on continuous-discrete Gaussian filtering.

    Note: this is specific for IVPs and does not apply without
    further considerations to, e.g., BVPs.

    Parameters
    ----------
    gaussfilt : gaussianfilter.GaussianFilter
        e.g. the return value of ivp_to_ukf(), ivp_to_ekf1().

    Notes
    -----
    - gaussfilt.dynamicmodel contains the prior,
    - gaussfilt.measurementmodel contains the information about the ODE right hand side function,
    - gaussfilt.initialdistribution contains the information about the initial values.
    """

    def __init__(self, ivp, gaussfilt, with_smoothing):
        if not isinstance(gaussfilt.dynamics_model, pnfs.statespace.Integrator):
            raise ValueError(
                "Please initialise a Gaussian filter with an Integrator (see filtsmooth.statespace)"
            )
        self.gfilt = gaussfilt
        self.sigma_squared_mle = 1.0
        self.with_smoothing = with_smoothing
        super().__init__(ivp=ivp, order=gaussfilt.dynamics_model.ordint)

    def initialise(self):
        return self.ivp.t0, self.gfilt.initrv

    def step(self, t, t_new, current_rv):
        """Gaussian IVP filter step as nonlinear Kalman filtering with zero data."""

        # Read the diffusion matrix; required for calibration / error estimation
        discrete_dynamics = self.gfilt.dynamics_model.discretise(t_new - t)
        proc_noise_cov = discrete_dynamics.proc_noise_cov_mat
        proc_noise_cov_cholesky = discrete_dynamics.proc_noise_cov_cholesky

        # 1. Predict
        pred_rv, _ = self.gfilt.dynamics_model.forward_rv(
            rv=current_rv, t=t, dt=t_new - t
        )

        # 2. Measure
        meas_rv, info = self.gfilt.measurement_model.forward_rv(
            rv=pred_rv, t=t_new, compute_gain=False
        )

        # 3. Estimate the diffusion (sigma squared)
        self.sigma_squared_mle = self._estimate_diffusion(meas_rv)

        # 3.1. Adjust the prediction covariance to include the diffusion
        pred_rv, _ = self.gfilt.dynamics_model.forward_rv(
            rv=current_rv, t=t, dt=t_new - t, _diffusion=self.sigma_squared_mle
        )

        # 3.2 Update the measurement covariance (measure again)
        meas_rv, info = self.gfilt.measurement_model.forward_rv(
            rv=pred_rv, t=t_new, compute_gain=True
        )

        # 4. Update
        zero_data = np.zeros(meas_rv.mean.shape)
        filt_rv, _ = self.gfilt.measurement_model.backward_realization(
            zero_data, pred_rv, rv_forwarded=meas_rv, gain=info["gain"]
        )

        # 5. Error estimate
        local_errors = self._estimate_local_error(
            pred_rv,
            t_new,
            self.sigma_squared_mle * proc_noise_cov,
            np.sqrt(self.sigma_squared_mle) * proc_noise_cov_cholesky,
        )
        err = np.linalg.norm(local_errors)

        return filt_rv, err

    def rvlist_to_odesol(self, times, rvs):
        """Create an ODESolution object."""

        kalman_posterior = pnfs.FilteringPosterior(
            times, rvs, self.gfilt.dynamics_model
        )

        return KalmanODESolution(kalman_posterior)

    def postprocess(self, odesol):
        """If specified (at initialisation), smooth the filter output."""
        if False:  # pylint: disable=using-constant-test
            # will become useful again for time-fixed diffusion models
            rvs = self._rescale(rvs)
        if self.with_smoothing is True:
            odesol = self._odesmooth(ode_solution=odesol)
        return odesol

    def _rescale(self, rvs):
        """Rescales covariances according to estimate sigma squared value."""
        rvs = [Normal(rv.mean, self.sigma_squared_mle * rv.cov) for rv in rvs]
        return rvs

    def _odesmooth(self, ode_solution, **kwargs):
        """Smooth out the ODE-Filter output.

        Be careful about the preconditioning: the GaussFiltSmooth object
        only knows the state space with changed coordinates!

        Parameters
        ----------
        filter_solution: ODESolution

        Returns
        -------
        smoothed_solution: ODESolution
        """
        smoothing_posterior = self.gfilt.smooth(ode_solution.kalman_posterior)
        return KalmanODESolution(smoothing_posterior)

    def _estimate_local_error(
        self,
        pred_rv,
        t_new,
        calibrated_proc_noise_cov,
        calibrated_proc_noise_cov_cholesky,
        **kwargs
    ):
        """Estimate the local errors.

        This corresponds to the approach in [1], implemented such that it is compatible
        with the EKF1 and UKF.

        References
        ----------
        .. [1] Schober, M., Särkkä, S. and Hennig, P..
            A probabilistic model for the numerical solution of initial
            value problems.
            Statistics and Computing, 2019.
        """
        local_pred_rv = Normal(
            pred_rv.mean,
            calibrated_proc_noise_cov,
            cov_cholesky=calibrated_proc_noise_cov_cholesky,
        )
        local_meas_rv, _ = self.gfilt.measure(local_pred_rv, t_new)
        error = local_meas_rv.cov.diagonal()
        return np.sqrt(np.abs(error))

    def _estimate_diffusion(self, meas_rv):
        """Estimate the dynamic diffusion parameter sigma_squared.

        This corresponds to the approach in [1], implemented such that it is compatible
        with the EKF1 and UKF.

        References
        ----------
        .. [1] Schober, M., Särkkä, S. and Hennig, P..
            A probabilistic model for the numerical solution of initial
            value problems.
            Statistics and Computing, 2019.
        """
        std_like = meas_rv.cov_cholesky
        whitened_res = np.linalg.solve(std_like, meas_rv.mean)
        ssq = whitened_res @ whitened_res / meas_rv.size
        return ssq

    @property
    def prior(self):
        return self.gfilt.dynamics_model
