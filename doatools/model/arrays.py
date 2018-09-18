from math import gcd
from collections import namedtuple
import numpy as np
import warnings
import copy

PerturbationParameterSet = namedtuple(
    'PerturbationParameterSet', ['parameters', 'known'])

PERTURBATION_TYPES = [
    'location_errors',
    'gain_errors',
    'phase_errors',
    'mutual_coupling'
]

class ArrayDesign:

    def __init__(self, locations, name, perturbations={}):
        '''
        Creates an custom array design.

        Implementation notice: array designs should be **immutable**. Because
        array design objects are passed around when computing steering matrices,
        weight functions, etc., having a mutable internal state leads to more
        complexities and potential unexpected results. Although the internal
        states are generally accessible in Python, please refrain from modifying
        them.

        Args:
            locations (ndarray): m x d matrix, where m is the number of elements
                and d can be 1, 2, or 3. The input ndarray is not copied and
                should never be changed after creating the array design.
            name (str): Name of the array design.
            perturbations (Dict): Array perturbations.
        '''
        if not isinstance(locations, np.ndarray):
            locations = np.array(locations)
        if locations.ndim > 2:
            raise ValueError('Expecting an 1D vector or a 2D matrix.')
        if locations.ndim == 1:
            locations = locations.reshape((-1, 1))
        elif locations.shape[1] > 3:
            raise ValueError('Array can only be 1D, 2D or 3D.')
        self._locations = locations
        self._name = name
        # Validate perturbations
        self._validate_perturbations(perturbations)
        self._perturbations = perturbations
    
    @property
    def name(self):
        '''
        Retrieves the name of this array.
        '''
        return self._name
    
    @property
    def element_count(self):
        '''
        (Deprecated) Retrieves the number of elements in the array.
        '''
        warnings.warn('Use size instead of element_count in the future.', DeprecationWarning)
        return self.size

    @property
    def size(self):
        '''
        Retrieves the number of elements in the array.
        '''
        return self._locations.shape[0]
    
    @property
    def element_locations(self):
        '''
        Retrives the nominal element locations.

        Returns:
            An M x d matrix, where M is the number of elements and d is the
            number of dimensions of the nominal array.
        '''
        return self._locations.copy()

    @property
    def is_perturbed(self):
        '''
        Returns if the array contains perturbations.
        '''
        return len(self._perturbations) > 0

    @property
    def ndim(self):
        '''
        Retrieves the number of dimensions of the nominal array.
        Perturbations do not affect this value.
        '''
        if self._locations.ndim == 1:
            return 1
        else:
            return self._locations.shape[1]

    def has_perturbation(self, ptype):
        '''
        Check if the array has the given type of perturbation.
        '''
        return ptype in self._perturbations
    
    @property
    def perturbations(self):
        '''
        Retrieves the dictionary of all perturbations. Do NOT modify.
        '''
        # Here we have a deep copy.
        return copy.deepcopy(self._perturbations)
    
    def _validate_perturbations(self, perturbations):
        for k, v in perturbations:
            if k not in PERTURBATION_TYPES:
                raise ValueError('Unsupported perturbation type "{0}".'.format(k))
            if not isinstance(v, tuple) and len(v) != 2:
                raise ValueError('Perturbation details should be specified by a two-element tuple.')
            # TODO: implement per perturbation type validations

    def get_perturbed_copy(self, perturbations, new_name=None):
        '''
        Returns a copy of this array design but with the specified
        perturbations. The specified perturbations will replace the existing
        ones.

        Args:
            perturbations (Dict): A dictionary containing the perturbation
                parameters. The keys should be among the following:
                * 'location_errors'
                * 'gain_errors' (relative, -0.2 means 0.8 * original gain)
                * 'phase_errors' (in radians)
                * 'mutual_coupling'
                The values are two-element tuples where the first element is an
                ndarray representing the parameters and the second element is
                a bool specifying whether these parameters are known in prior.
            new_name (str): An optional new name for the resulting array design.
                If not provided, the name of the original array design will be
                used.
        '''
        design = self.get_perturbation_free_copy(new_name)
        # Merge perturbation parameters.
        new_perturbations = {**design._perturbations, **perturbations}
        self._validate_perturbations(new_perturbations)
        design._perturbations = new_perturbations
        return design

    def get_perturbation_free_copy(self, new_name=None):
        '''
        Returns a perturbation-free copy of this array design.

        Args:
            new_name (str): An optional new name for the resulting array design.
                If not provided, the name of the original array design will be
                used.
        '''
        if new_name is None:
            new_name = self._name
        design = copy.copy(self)
        design._perturbation = {}
        design._name = new_name
        return design

    def steering_matrix(self, sources, wavelength, compute_derivatives=False,
                        perturbations='all'):
        '''
        Creates the steering matrix for the given DOAs.

        Note: the steering matrix calculation is bound to array designs.
        This is a generic implementation, which can be overridden for special
        types of arrays.

        Args:
            sources: An instance of SourcePlacement. 
                Notes:
                1. 1D arrays are placed along the x-axis. 2D arrays are placed
                   within the xy-plane.
                2. If you pass in 1D DOAs for an 2D or 3D array, these DOAs will
                   be assumed to be within the xy-plane:
                    azimuth = pi/2 - 1D DOA (broadside -> azimuth)
                    elevation = 0 (with xy-plane)
            wavelength: Wavelength of the carrier wave.
            compute_derivatives: If set to True, also outputs the derivative
                matrix DA with respect to the DOAs, where the k-th column of
                DA is the derivative of the k-th column of A with respect to the
                k-th DOA. DA is used when computing the CRBs. Only available to
                1D DOAs.
            perturbations: Specifies which perturbations are considered when
                constructing the steering matrix:
                * 'all' - All perturbations are considered. This is the default
                    value.
                * 'known' - Only known perturbations (we have prior knowledge of
                    the perturbation parameters) are considered. This option is
                    used by DOA estimators when the exact knowledge of these
                    perturbations are known in prior.
                * 'none' - None of the perturbations are considered.
        '''
        # Filter perturbations.
        if perturbations == 'all':
            perturb_dict = self._perturbations
        elif perturbations == 'known':
            perturb_dict = {k: v for k, v in self._perturbations.items() if v.known}
        elif perturbations == 'none':
            perturb_dict = {}
        else:
            raise ValueError('Perturbation can only be "all", "known", or "none".')
        
        actual_ndim = self.ndim
        # Apply the location errors if exist.
        if 'location_errors' in perturb_dict:
            pos_err_dim = perturb_dict['location_errors'].parameters.shape[1]
            if pos_err_dim <= actual_ndim:
                # It is possible that the location errors only exist along the
                # first one or two axis.
                actual_locations = self._locations.copy()
                actual_locations[:, :pos_err_dim] += perturb_dict['location_errors'].parameters
            else:
                # Actual dimension is higher. This happens if a linear array,
                # which is 1D, has location errors along both x- and y-axis.
                actual_locations = perturb_dict['location_errors'].parameters.copy()
                actual_locations[:, :actual_ndim] += self._locations
                actual_ndim = pos_err_dim
        else:
            actual_locations = self._locations

        # Compute the steering matrix
        T = sources.phase_delay_matrix(actual_locations, wavelength, compute_derivatives)
        if compute_derivatives:
            A = np.exp(1j * T[0])
            DA = [A * (1j * X) for X in T[1:]]
        else:
            A = np.exp(1j * T)
        
        # Apply other perturbations
        if 'gain_errors' in perturb_dict:
            gain_coeff = 1. + perturb_dict['gain_errors'].parameters
            A = gain_coeff * A
            if compute_derivatives:
                DA = [gain_coeff * X for X in DA]
        if 'phase_errors' in perturb_dict:
            phase_coeff = np.exp(1j * perturb_dict['phase_errors'].parameters)
            A = phase_coeff * A
            if compute_derivatives:
                DA = [phase_coeff * X for X in DA]
        if 'mutual_coupling' in perturb_dict:
            A = perturb_dict['mutual_coupling'].parameters @ A
            if compute_derivatives:
                DA = [perturb_dict['mutual_coupling'].parameters @ X for X in DA]

        if compute_derivatives:
            return (A,) + tuple(DA)
        else:
            return A

    def get_measurements(self, wavelength, source_placement, n_snapshots,
                         source_signal=None, noise_signal=None,
                         compute_covariance=False):
        '''
        Gets an M x N matrix Y of measurement vectors using the following model:

        Y = AS + E,

        where A is the steering matrix, S consists of source signals and E
        consists of noise signals.
        '''
        A = self.steering_matrix(source_placement, wavelength)
        S = source_signal.emit(n_snapshots)
        N = noise_signal.emit(n_snapshots)
        Y = A @ S + N
        if compute_covariance:
            R = (Y @ Y.conj().T) / n_snapshots
            return Y, R
        else:
            return Y


class GridBasedArrayDesign(ArrayDesign):

    def __init__(self, indices, d0, name, **kwargs):
        '''
        Creates an array design where each elements is placed on a predefined
        grid with grid size d0.

        Args:
            indices (ndarray): m x d matrix denoting the grid indices
                of each element. e.g., if indices is [1, 2, 3,], then the
                actual locations will be [d0, 2*d0, 3*d0]. The input ndarray is
                not copied and should never be changed after creating this array
                design.
            d0 (float): Grid size (or base inter-element spacing).
            name (str): Name of the array design.
            **kwargs: Other keyword arguments supported by ArrayDesign.
        '''
        super().__init__(indices * d0, name, **kwargs)
        self._element_indices = indices
        self._d0 = d0

    @property
    def d0(self):
        return self._d0

    @property
    def element_indices(self):
        '''
        Retrives the element indices.
        '''
        return self._element_indices.copy()

class UniformLinearArray(GridBasedArrayDesign):

    def __init__(self, n, d0, name=None, **kwargs):
        '''
        Creates an n-element uniform linear array (ULA) along the x-axis, where
        the first sensor is placed at the origin.

        Args:
            n (int): Number of elements.
            d0 (float): Fundamental inter-element spacing (usually smallest).
            name (str): Name of the array design.
            **kwargs: Other keyword arguments supported by ArrayDesign.
        '''
        if name is None:
            name = 'ULA ' + str(n)
        super().__init__(np.arange(n).reshape((-1, 1)), d0, name, **kwargs)

class NestedArray(GridBasedArrayDesign):

    def __init__(self, n1, n2, d0, name=None, **kwargs):
        '''
        Creates an 1D nested array.

        Args:
            n1 (int): Parameter N1.
            n2 (int): Parameter N2.
            d0 (float): Fundamental inter-element spacing (usually smallest).
            name (str): Name of the array design.
            **kwargs: Other keyword arguments supported by ArrayDesign.

        References:
        [1] P. Pal and P. P. Vaidyanathan, "Nested arrays: A novel approach to
            array processing with enhanced degrees of freedom," IEEE
            Transactions on Signal Processing, vol. 58, no. 8, pp. 4167-4181,
            Aug. 2010.
        '''
        if name is None:
            name = 'Nested ({0},{1})'.format(n1, n2)
        indices = np.concatenate((
            np.arange(0, n1),
            np.arange(1, n2 + 1) * (n1 + 1) - 1
        ))
        self._n1 = n1
        self._n2 = n2
        super().__init__(indices.reshape((-1, 1)), d0, name, **kwargs)

    @property
    def n1(self):
        '''
        Retrieves the parameter, N1, used when creating this nested array.
        '''
        return self._n1
    
    @property
    def n2(self):
        '''
        Retrieves the parameter, N2, used when creating this nested array.
        '''
        return self._n2

class CoPrimeArray(GridBasedArrayDesign):

    def __init__(self, m, n, d0, mode='2m', name=None, **kwargs):
        '''
        Creates an 1D co-prime array.

        Args:
            m (int): The smaller number in the co-prime pair.
            n (int): The larger number in the co-prime pair.
            d0 (float): Fundamental inter-element spacing (usually smallest).
            mode (str): Either 'm' or '2m'.
            name (str): Name of the array design.
            **kwargs: Other keyword arguments supported by ArrayDesign.
        
        References:
        [1] P. Pal and P. P. Vaidyanathan, "Coprime sampling and the music
            algorithm," in 2011 Digital Signal Processing and Signal Processing
            Education Meeting (DSP/SPE), 2011, pp. 289-294.
        '''
        if name is None:
            name = 'Co-prime ({0},{1})'.format(m, n)
        if m > n:
            warnings.warn('m > n. Swapped.')
            m, n = n, m
        if gcd(m, n) != 1:
            raise ValueError('{0} and {1} are not co-prime.'.format(m, n))
        self._coprime_pair = (m, n)
        mode = mode.lower()
        if mode == '2m':
            indices = np.concatenate((
                np.arange(0, n) * m,
                np.arange(1, 2*m) * n
            ))
        elif mode == 'm':
            indices = np.concatenate((
                np.arange(0, n) * m,
                np.arange(1, m) * n
            ))
        else:
            raise ValueError('Unknown mode "{0}"'.format(mode))
        self._mode = mode
        super().__init__(indices.reshape((-1, 1)), d0, name, **kwargs)

    @property
    def coprime_pair(self):
        '''
        Retrieves the co-prime pair used when creating this co-prime array.
        '''
        return self._coprime_pair

    @property
    def mode(self):
        '''
        Retrieves the mode used when creating this co-prime array.
        '''
        return self._mode

class UniformCircularArray(ArrayDesign):

    def __init__(self, n, r, name=None, **kwargs):
        '''
        Creates a uniform circular array centered at the origin, in the
        xy-plane.

        Args:
            n (int): Number of elements.
            r (float): Radius of the circle.
            name (str): Name of the array design.
            **kwargs: Other keyword arguments supported by ArrayDesign.
        '''
        if name is None:
            name = 'UCA ' + str(n)
        self._r = r
        theta = np.linspace(0., np.pi * (2.0 - 2.0 / n), n)
        locations = np.vstack((r * np.cos(theta), r * np.sin(theta))).T
        super().__init__(locations, name)

    @property
    def radius(self):
        '''
        Retrieves the radius of the uniform circular array.
        '''
        return self._r