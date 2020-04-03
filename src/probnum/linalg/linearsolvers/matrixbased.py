"""
Matrix-based probabilistic linear solvers.

Implementations of matrix-based linear solvers which perform inference on the matrix or its inverse given linear
observations.
"""
import warnings
import abc

import numpy as np
import GPy

from probnum import prob
from probnum.linalg import linops


class ProbabilisticLinearSolver(abc.ABC):
    """
    An abstract base class for probabilistic linear solvers.

    This class is designed to be subclassed with new (probabilistic) linear solvers, which implement a ``.solve()``
    method. Objects of this type are instantiated in wrapper functions such as :meth:``problinsolve``.

    Parameters
    ----------
    A : array-like or LinearOperator or RandomVariable, shape=(n,n)
        A square matrix or linear operator. A prior distribution can be provided as a
        :class:`~probnum.prob.RandomVariable`. If an array or linear operator is given, a prior distribution is
        chosen automatically.
    b : array_like, shape=(n,) or (n, nrhs)
        Right-hand side vector or matrix in :math:`A x = b`.
    """

    def __init__(self, A, b):
        self.A = A
        self.b = b

    def _check_convergence(self, iter, maxiter, resid, atol, rtol):
        """
        Check convergence of a linear solver.

        Evaluates a set of convergence criteria based on its input arguments to decide whether the iteration has converged.

        Parameters
        ----------
        iter : int
            Current iteration of solver.
        maxiter : int
            Maximum number of iterations
        resid : array-like
            Residual vector :math:`\\lVert r_i \\rVert = \\lVert Ax_i - b \\rVert` of the current iteration.
        atol : float
            Absolute residual tolerance. Stops if :math:`\\lVert r_i \\rVert < \\text{atol}`.
        rtol : float
            Relative residual tolerance. Stops if :math:`\\lVert r_i \\rVert < \\text{rtol} \\lVert b \\rVert`.

        Returns
        -------
        has_converged : bool
            True if the method has converged.
        convergence_criterion : str
            Convergence criterion which caused termination.
        """
        # maximum iterations
        if iter >= maxiter:
            warnings.warn(message="Iteration terminated. Solver reached the maximum number of iterations.")
            return True, "maxiter"
        # residual below error tolerance
        elif np.linalg.norm(resid) <= atol:
            return True, "resid_atol"
        elif np.linalg.norm(resid) <= rtol * np.linalg.norm(self.b):
            return True, "resid_rtol"
        # uncertainty-based
        # todo: based on posterior contraction
        else:
            return False, ""

    def solve(self, callback=None, **kwargs):
        """
        Solve the linear system :math:`Ax=b`.

        Parameters
        ----------
        callback : function, optional
            User-supplied function called after each iteration of the linear solver. It is called as
            ``callback(xk, sk, yk, alphak, resid, **kwargs)`` and can be used to return quantities from the iteration.
            Note that depending on the function supplied, this can slow down the solver.
        kwargs
            Key-word arguments adjusting the behaviour of the ``solve`` iteration. These are usually convergence
            criteria.

        Returns
        -------
        x : RandomVariable, shape=(n,) or (n, nrhs)
            Approximate solution :math:`x` to the linear system. Shape of the return matches the shape of ``b``.
        A : RandomVariable, shape=(n,n)
            Posterior belief over the linear operator.
        Ainv : RandomVariable, shape=(n,n)
            Posterior belief over the linear operator inverse :math:`H=A^{-1}`.
        info : dict
            Information on convergence of the solver.

        """
        raise NotImplementedError


class GeneralMatrixBasedSolver(ProbabilisticLinearSolver):
    """
    Solver iteration of a (general) matrix-based probabilistic linear solver.

    Parameters
    ----------
    """

    def __init__(self, A, b):
        raise NotImplementedError
        # super().__init__(A=A, b=b)

    def solve(self, callback=None, maxiter=None, atol=None):
        raise NotImplementedError


class SymmetricMatrixBasedSolver(ProbabilisticLinearSolver):
    """
    Solver iteration of the symmetric probabilistic linear solver.

    Implements the solve iteration of the symmetric matrix-based probabilistic linear solver described in [1]_ and [2]_.

    Parameters
    ----------
    A : array-like or LinearOperator or RandomVariable, shape=(n,n)
        The square matrix or linear operator of the linear system.
    b : array_like, shape=(n,) or (n, nrhs)
        Right-hand side vector or matrix in :math:`A x = b`.
    A_mean : array-like or LinearOperator
        Mean of the prior distribution on the linear operator :math:`A`.
    A_covfactor : array-like or LinearOperator
        The Kronecker factor :math:`W_A` of the covariance :math:`\\operatorname{Cov}(A) = W_A \\otimes_s W_A` of
        :math:`A`.
    Ainv_mean : array-like or LinearOperator
        Mean of the prior distribution on the linear operator :math:`A^{-1}`.
    Ainv_covfactor : array-like or LinearOperator
        The Kronecker factor :math:`W_H` of the covariance :math:`\\operatorname{Cov}(H) = W_H \\otimes_s W_H` of
        :math:`H = A^{-1}`.

    Returns
    -------
    A : RandomVariable
        Posterior belief over the linear operator.
    Ainv : RandomVariable
        Posterior belief over the inverse linear operator.
    x : RandomVariable
        Posterior belief over the solution of the linear system.
    info : dict
        Information about convergence and the solution.

    References
    ----------
    .. [1] Wenger, J. and Hennig, P., Probabilistic Linear Solvers for Machine Learning, 2020
    .. [2] Hennig, P., Probabilistic Interpretation of Linear Solvers, *SIAM Journal on Optimization*, 2015, 25, 234-260
    """

    def __init__(self, A, b, A_mean, A_covfactor, Ainv_mean, Ainv_covfactor):
        self.A_mean = A_mean
        self.A_covfactor = A_covfactor
        self.Ainv_mean = Ainv_mean
        self.Ainv_covfactor = Ainv_covfactor
        self.x = Ainv_mean @ b
        self.S = []
        self.Y = []
        self.sy = []
        super().__init__(A=A, b=b)

    def _calibrate_uncertainty(self):
        """
        Calibrate uncertainty based on the Rayleigh coefficients

        A regression model for the log-Rayleigh coefficient is built based on the collected observations. The degrees of
        freedom in the covariance of A and H are set according to the predicted log-Rayleigh coefficient for the
        remaining unexplored dimensions.
        """
        # Transform to arrays
        _sy = np.squeeze(np.array(self.sy))
        _S = np.squeeze(np.array(self.S)).T
        _Y = np.squeeze(np.array(self.Y)).T

        if self.iter_ > 5:  # only calibrate if enough iterations for a regression model have been performed
            # Rayleigh quotient
            iters = np.arange(self.iter_)
            logR = np.log(_sy) - np.log(np.einsum('ij,ij->j', _S, _S))

            # Least-squares fit for y intercept
            x_mean = np.mean(iters)
            y_mean = np.mean(logR)
            beta1 = np.sum((iters - x_mean) * (logR - y_mean)) / np.sum((iters - x_mean) ** 2)
            beta0 = y_mean - beta1 * x_mean

            # Log-Rayleigh quotient regression
            mf = GPy.mappings.linear.Linear(1, 1)
            k = GPy.kern.RBF(input_dim=1, lengthscale=1, variance=1)
            m = GPy.models.GPRegression(iters[:, None], (logR - beta0)[:, None], kernel=k, mean_function=mf)
            m.optimize(messages=False)

            # Predict Rayleigh quotient
            remaining_dims = np.arange(self.iter_, self.A.shape[0])[:, None]
            GP_pred = m.predict(remaining_dims)
            R_pred = np.exp(GP_pred[0].ravel() + beta0)

            # Set scale
            Phi = linops.ScalarMult(shape=self.A.shape, scalar=np.asscalar(np.mean(R_pred)))
            Psi = linops.ScalarMult(shape=self.A.shape, scalar=np.asscalar(np.mean(1 / R_pred)))

        else:
            Phi = None
            Psi = None

        return Phi, Psi

    def _create_output_randvars(self, S=None, Y=None, Phi=None, Psi=None):
        """Return output random variables x, A, Ainv from their means and covariances."""

        _A_covfactor = self.A_covfactor
        _Ainv_covfactor = self.Ainv_covfactor

        # Set degrees of freedom based on uncertainty calibration in unexplored space
        if Phi is not None:
            def _mv(x):
                def _I_S_fun(x):
                    return x - S @ np.linalg.solve(S.T @ S, S.T @ x)

                return _I_S_fun(Phi @ _I_S_fun(x))

            I_S_Phi_I_S_op = linops.LinearOperator(shape=self.A.shape, matvec=_mv)
            _A_covfactor = self.A_covfactor + I_S_Phi_I_S_op

        if Psi is not None:
            def _mv(x):
                def _I_Y_fun(x):
                    return x - Y @ np.linalg.solve(Y.T @ Y, Y.T @ x)

                return _I_Y_fun(Psi @ _I_Y_fun(x))

            I_Y_Psi_I_Y_op = linops.LinearOperator(shape=self.A.shape, matvec=_mv)
            _Ainv_covfactor = self.Ainv_covfactor + I_Y_Psi_I_Y_op

        # Create output random variables
        A = prob.RandomVariable(shape=self.A_mean.shape,
                                dtype=float,
                                distribution=prob.Normal(mean=self.A_mean,
                                                         cov=linops.SymmetricKronecker(
                                                             A=_A_covfactor)))
        cov_Ainv = linops.SymmetricKronecker(A=_Ainv_covfactor)
        Ainv = prob.RandomVariable(shape=self.Ainv_mean.shape,
                                   dtype=float,
                                   distribution=prob.Normal(mean=self.Ainv_mean, cov=cov_Ainv))
        # Induced distribution on x via Ainv
        # Exp = x = A^-1 b, Cov = 1/2 (W b'Wb + Wbb'W)
        Wb = _Ainv_covfactor @ self.b
        bWb = np.squeeze(Wb.T @ self.b)

        def _mv(x):
            return 0.5 * (bWb * _Ainv_covfactor @ x + Wb @ (Wb.T @ x))

        cov_op = linops.LinearOperator(shape=np.shape(_Ainv_covfactor), dtype=float,
                                       matvec=_mv, matmat=_mv)

        x = prob.RandomVariable(shape=(self.A_mean.shape[0],),
                                dtype=float,
                                distribution=prob.Normal(mean=self.x.ravel(), cov=cov_op))
        return x, A, Ainv

    def _mean_update(self, u, v):
        """Linear operator implementing the symmetric rank 2 mean update (+= uv' + vu')."""

        def mv(x):
            return u @ (v.T @ x) + v @ (u.T @ x)

        def mm(X):
            return u @ (v.T @ X) + v @ (u.T @ X)

        return linops.LinearOperator(shape=self.A_mean.shape, matvec=mv, matmat=mm)

    def _covariance_update(self, u, Ws):
        """Linear operator implementing the symmetric rank 2 covariance update (-= Ws u^T)."""

        def mv(x):
            return u @ (Ws.T @ x)

        def mm(X):
            return u @ (Ws.T @ X)

        return linops.LinearOperator(shape=self.A_mean.shape, matvec=mv, matmat=mm)

    def solve(self, callback=None, maxiter=None, atol=None, rtol=None, calibrate=True):
        # initialization
        self.iter_ = 0
        resid = self.A @ self.x - self.b

        # iteration with stopping criteria
        while True:
            # check convergence
            _has_converged, _conv_crit = self._check_convergence(iter=self.iter_, maxiter=maxiter,
                                                                 resid=resid, atol=atol, rtol=rtol)
            if _has_converged:
                break

            # compute search direction (with implicit reorthogonalization)
            search_dir = - self.Ainv_mean @ resid
            self.S.append(search_dir)

            # perform action and observe
            obs = self.A @ search_dir
            self.Y.append(obs)

            # compute step size
            sy = search_dir.T @ obs
            step_size = - (search_dir.T @ resid) / sy
            self.sy.append(sy)

            # step and residual update
            self.x = self.x + step_size * search_dir
            resid = resid + step_size * obs

            # (symmetric) mean and covariance updates
            Vs = self.A_covfactor @ search_dir
            delta_A = obs - self.A_mean @ search_dir
            u_A = Vs / (search_dir.T @ Vs)
            v_A = delta_A - 0.5 * (search_dir.T @ delta_A) * u_A

            Wy = self.Ainv_covfactor @ obs
            delta_Ainv = search_dir - self.Ainv_mean @ obs
            u_Ainv = Wy / (obs.T @ Wy)
            v_Ainv = delta_Ainv - 0.5 * (obs.T @ delta_Ainv) * u_Ainv

            # rank 2 mean updates (+= uv' + vu')
            # TODO: Operator form may cause stack size issues for too many iterations
            self.A_mean = linops.aslinop(self.A_mean) + self._mean_update(u=u_A, v=v_A)
            self.Ainv_mean = linops.aslinop(self.Ainv_mean) + self._mean_update(u=u_Ainv, v=v_Ainv)

            # rank 1 covariance kronecker factor update (-= u_A(Vs)' and -= u_Ainv(Wy)')
            self.A_covfactor = linops.aslinop(self.A_covfactor) - self._covariance_update(u=u_A, Ws=Vs)
            self.Ainv_covfactor = linops.aslinop(self.Ainv_covfactor) - self._covariance_update(u=u_Ainv,
                                                                                                Ws=Wy)

            # iteration increment
            self.iter_ += 1

            # callback function used to extract quantities from iteration
            if callback is not None:
                # Phi, Psi = self._calibrate_uncertainty()
                xk, Ak, Ainvk = self._create_output_randvars(S=np.squeeze(np.array(self.S)).T,
                                                             Y=np.squeeze(np.array(self.Y)).T,
                                                             Phi=None,  # Phi,
                                                             Psi=None)  # Psi)
                callback(xk=xk, Ak=Ak, Ainvk=Ainvk, sk=search_dir, yk=obs, alphak=step_size, resid=resid)

        # Calibrate uncertainty
        if calibrate:
            Phi, Psi = self._calibrate_uncertainty()
        else:
            Phi = None
            Psi = None

        # Create output random variables
        x, A, Ainv = self._create_output_randvars(S=np.squeeze(np.array(self.S)).T,
                                                  Y=np.squeeze(np.array(self.Y)).T,
                                                  Phi=Phi,
                                                  Psi=Psi)

        # Log information on solution
        info = {
            "iter": self.iter_,
            "maxiter": maxiter,
            "resid_l2norm": np.linalg.norm(resid, ord=2),
            "conv_crit": _conv_crit,
            "matrix_cond": None  # TODO: matrix condition from solver (see scipy solvers)
        }

        return x, A, Ainv, info