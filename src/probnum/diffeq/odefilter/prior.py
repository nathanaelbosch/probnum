"""
Continuous-Time priors for ODE solvers.

Currently, they are only relevant in the context of ODEs.
If needed in a more general setting, it is easy to move
them to statespace module (->thoughts?)

Matern will be easy to implement, just reuse the template
provided by IOUP and change parameters
"""
import numpy as np
from scipy.special import binom   # for matern!

from probnum.prob import RandomVariable
from probnum.prob.distributions import Normal
from probnum.filtsmooth.statespace.continuous import LTISDEModel


__all__ = ["IBM", "IOUP", "Matern"]


class IBM(LTISDEModel):
    """
    IBM(q) (integrated Brownian motion of order q) prior:

    F = I_d \\otimes F
    L = I_d \\otimes L = I_d \\otimes diffconst*(0, ..., 1)
    Q = I_d \\otimes I_(q+1)
    """

    def __init__(self, ordint, spatialdim, diffconst):
        """
        ordint : this is "q"
        spatialdim : d
        diffconst : sigma
        """
        self.ordint = ordint
        self.spatialdim = spatialdim
        self.diffconst = diffconst
        driftmat = _dynamat_ibm(self.ordint, self.spatialdim)
        forcevec = np.zeros(len(driftmat))
        dispvec = _dispvec_ibm_ioup_matern(self.ordint, self.spatialdim, diffconst)
        diffmat = np.eye(self.spatialdim * (self.ordint + 1))
        super().__init__(driftmat, forcevec, dispvec, diffmat)

    def chapmankolmogorov(self, start, stop, step, randvar, *args, **kwargs):
        """
        Overwrites CKE solution with closed form according to IBM.
        "step" variable is obsolent here and is ignored.
        """
        mean, covar = randvar.mean(), randvar.cov()
        ah = self._ah_ibm(start, stop)
        qh = self._qh_ibm(start, stop)
        mpred = ah @ mean
        cpred = ah @ covar @ ah.T + qh
        return RandomVariable(distribution=Normal(mpred, cpred))

    def _ah_ibm(self, start, stop):
        """
        Computes A(h)
        """

        def element(stp, rw, cl):
            """Closed form for A(h)_ij"""
            if rw <= cl:
                return stp ** (cl - rw) / np.math.factorial(cl - rw)
            else:
                return 0.0

        step = stop - start
        ah_1d = np.array([[element(step, row, col)
                           for col in range(self.ordint + 1)]
                          for row in range(self.ordint + 1)])
        return np.kron(np.eye(self.spatialdim), ah_1d)

    def _qh_ibm(self, start, stop):
        """
        Computes Q(h)
        """

        def element(stp, ordint, rw, cl, dconst):
            """Closed form for Q(h)_ij"""
            idx = 2 * ordint + 1 - rw - cl
            fact_rw = np.math.factorial(ordint - rw)
            fact_cl = np.math.factorial(ordint - cl)
            return dconst ** 2 * (stp ** idx) / (idx * fact_rw * fact_cl)

        step = stop - start
        qh_1d = np.array([[element(step, self.ordint, row, col, self.diffconst)
                           for col in range(self.ordint + 1)]
                          for row in range(self.ordint + 1)])
        return np.kron(np.eye(self.spatialdim), qh_1d)


def _dynamat_ibm(ordint, spatialdim):
    """
    Returns I_d \\otimes F
    """
    dynamat = np.diag(np.ones(ordint), 1)
    return np.kron(np.eye(spatialdim), dynamat)


class IOUP(LTISDEModel):
    """
    IOUP(q) prior:

    F = I_d \\otimes F
    L = I_d \\otimes L = I_d \\otimes diffconst*(0, ..., 1)
    Q = I_d \\otimes I_(q+1)
    """

    def __init__(self, ordint, spatialdim, driftspeed, diffconst):
        """
        ordint : this is "q"
        spatialdim : d
        driftspeed : float > 0; (lambda; note that -lambda ("minus"-lambda)
            is used in the OU equation!!
        diffconst : sigma
        """
        self.ordint = ordint
        self.spatialdim = spatialdim
        self.driftspeed = driftspeed
        self.diffconst = diffconst
        driftmat = _dynamat_ioup(self.ordint, self.spatialdim, self.driftspeed)
        forcevec = np.zeros(len(driftmat))
        dispvec = _dispvec_ibm_ioup_matern(self.ordint, self.spatialdim, diffconst)
        diffmat = np.eye(self.spatialdim * (self.ordint + 1))
        super().__init__(driftmat, forcevec, dispvec, diffmat)


def _dynamat_ioup(ordint, spatialdim, driftspeed):
    """
    Returns I_d \\otimes F
    """
    dynamat = np.diag(np.ones(ordint), 1)
    dynamat[-1, -1] = -driftspeed
    return np.kron(np.eye(spatialdim), dynamat)






class Matern(LTISDEModel):
    """
    Matern(q) prior --> Matern process with reg. q+0.5
    and hence, with matrix size q+1

    F = I_d \\otimes F
    L = I_d \\otimes L = I_d \\otimes diffconst*(0, ..., 1)
    Q = I_d \\otimes I_(q+1)
    """

    def __init__(self, ordint, spatialdim, lengthscale, diffconst):
        """
        ordint : this is "q"
        spatialdim : d
        lengthscale : used as 1/lengthscale, remember that!
        diffconst : sigma

        """
        self.ordint = ordint
        self.spatialdim = spatialdim
        self.lengthscale = lengthscale
        self.diffconst = diffconst
        driftmat = _dynamat_matern(self.ordint, self.spatialdim, self.lengthscale)
        forcevec = np.zeros(len(driftmat))
        dispvec = _dispvec_ibm_ioup_matern(self.ordint, self.spatialdim, diffconst)
        diffmat = np.eye(self.spatialdim * (self.ordint + 1))
        super().__init__(driftmat, forcevec, dispvec, diffmat)


def _dynamat_matern(ordint, spatialdim, lengthscale):
    """
    Returns I_d \\otimes F
    """
    dynamat = np.diag(np.ones(ordint), 1)
    nu = ordint + 0.5
    D, lam = ordint + 1,  np.sqrt(2*nu) / lengthscale
    dynamat[-1, :] = np.array([-binom(D, i)*lam**(D-i) for i in range(D)])
    return np.kron(np.eye(spatialdim), dynamat)


def _dispvec_ibm_ioup_matern(ordint, spatialdim, diffconst):
    """
    Returns I_D \otimes L
    diffconst = sigma**2
    """
    dispvec = diffconst * np.eye(ordint + 1)[:, -1]
    return np.kron(np.ones(spatialdim), dispvec)


