# -*- coding: utf-8 -*-
from __future__ import division
import numpy as np
from scipy.ndimage import map_coordinates
from scipy.interpolate import UnivariateSpline, splrep, splev
from scipy.optimize import leastsq

import abel

from abel import _deprecated, _deprecate

#########################################################################
# circularize.py
#
# Image circularization by following peak intensity vs angle
# see https://github.com/PyAbel/PyAbel/issues/186 for discussion
# and https://github.com/PyAbel/PyAbel/pull/195
#
# Steve Gibson and Dan Hickstein - ideas/code
# Jason Gascooke - ideas
#
# February 2017
#########################################################################


def circularize_image(IM, method="lsq", origin=None, radial_range=None,
                      dr=0.5, dt=0.5, smooth=_deprecated, ref_angle=None,
                      inverse=False, return_correction=False, tol=0,
                      center=_deprecated):
    r"""
    Corrects image distortion on the basis that the structure should be
    circular.

    This is a simplified radial scaling version of the algorithm described in
    J. R. Gascooke, S. T. Gibson, W. D. Lawrance,
    "A 'circularisation' method to repair deformations and determine the centre
    of velocity map images",
    `J. Chem. Phys. 147, 013924 (2017)
    <https://dx.doi.org/10.1063/1.4981024>`_.

    This function is especially useful for correcting the image obtained with
    a velocity-map-imaging spectrometer, in the case where there is distortion
    of the Newton sphere (ring) structure due to an imperfect electrostatic
    lens or stray electromagnetic fields. The correction allows the
    highest-resolution 1D photoelectron distribution to be extracted.

    The algorithm splits the image into "slices" at many different angles
    (set by **dt**) and compares the radial intensity profile of adjacent slices.
    A scaling factor is found which aligns each slice profile with the previous
    slice. The image is then corrected using a spline function that smoothly
    connects the discrete scaling factors as a continuous function of angle.

    This circularization algorithm should only be applied to a well-centered
    image, otherwise use the **origin** keyword (described below) to
    center it.


    Parameters
    ----------
    IM : numpy 2D array
        Image to be circularized.

    method : str
        Method used to determine the radial correction factor to align slice
        profiles:

        ``argmax``
            compare intensity-profile.argmax() of each radial slice.
            This method is quick and reliable, but it assumes that
            the radial intensity profile has an obvious maximum.
            The positioning is limited to the nearest pixel.

        ``lsq``
            minimize the difference between a slice intensity-profile
            with its adjacent slice.
            This method is slower and may fail to converge, but it
            may be applied to images with any (circular) structure.
            It aligns the slices with sub-pixel precision.

    origin : float tuple, str or None
        Pre-center image using :func:`abel.tools.center.center_image`.
        May be an explicit (row, column) tuple or a method name: ``'com'``,
        ``'convolution'``, ``'gaussian;``, ``'image_center'``, ``'slice'``.
        ``None`` (default) assumes that the image is already centered.

    radial_range : tuple or None
        Limit slice comparison to the radial range tuple (rmin, rmax), in
        pixels, from the image origin. Use to determine the distortion
        correction associated with particular peaks. It is recommended to
        select a region of your image where the signal-to-noise ratio is
        highest, with sharp persistent (in angle) features.

    dr : float
        Radial grid size for the polar coordinate image, default = 0.5 pixel.
        This is passed to :func:`abel.tools.polar.reproject_image_into_polar`.

        Small values may improve the distortion correction, which is often of
        sub-pixel dimensions, at the cost of reduced signal to noise for the
        slice intensity profile. As a general rule, `dr` should be
        significantly smaller than the radial "feature size" in the image.

    dt : float
        Angular grid size. This sets the number of radial slices, given by
        :math:`2\pi/dt`. Default = 0.1, ~ 63 slices. More slices, using
        smaller `dt`, may provide a more detailed angular variation of the
        correction, at the cost of greater signal to noise in the correction
        function.

        Also passed to :func:`abel.tools.polar.reproject_image_into_polar`.

    smooth : float
        Deprecated, use **tol** instead. The relationship is
        **smooth** = `N`\ :sub:`angles` × **tol**\ :sup:`2`,
        where `N`\ :sub:`angles` is the number of slices (see **dt**).

    ref_angle : None or float
        Reference angle for which radial coordinate is unchanged.
        Angle varies between :math:`-\pi` and :math:`\pi`, with zero angle
        vertical.

        ``None`` uses :func:`numpy.mean` of the radial correction function,
        which attempts to maintain the same average radial scaling. This
        approximation is likely valid, unless you know for certain that a
        specific angle of your image corresponds to an undistorted image.

    inverse : bool
        Apply an inverse Abel transform to the `polar`-coordinate image, to
        remove the background intensity. This may improve the signal-to-noise
        ratio, allowing the weaker intensity featured to be followed in angle.

        Note that this step is only for the purposes of allowing the algorithm
        to better follow peaks in the image. It does not affect the final
        image that is returned, except for (hopefully) slightly improving the
        precision of the distortion correction.

    return_correction : bool
        Additional outputs, as describe below.

    tol : float
        Root-mean-square (RMS) fitting tolerance for the spline function. At
        the default zero value, the spline interpolates between the discrete
        scaling factors. At larger values, a smoother spline is found such that
        its RMS deviation from the discrete scaling factors does not exceed
        this number. For example, ``tol=0.01`` means 1% RMS tolerance for the
        radial scaling correction. At very large tolerances, the spline
        degenerates to a constant, the average of the discrete scaling factors.

        Typically, **tol** may remain zero (use interpolation), but noisy data
        may require some smoothing, since the found discrete scaling factors
        can have noticeable errors. To examine the relative scaling factors and
        how well they are represented by the spline function, use the option
        ``return_correction=True``.

    Returns
    -------
    IMcirc : numpy 2D array
        Circularized version of the input image, same size as input.

    The following values are returned if ``return_correction=True``:

    angles : numpy 1D array
        Mid-point angle (radians) of each image slice.

    radial_correction : numpy 1D array
        Radial correction scale factor at each angular slice.

    radial_correction_function : function(numpy 1D array)
        Function that may be used to evaluate the radial correction at any
        angle.

    """
    if center is not _deprecated:
        _deprecate('abel.tools.circularize.circularize_image() '
                   'argument "center" is deprecated, use "origin" instead.')
        origin = center

    if origin is not None:
        # convenience function for the case image is not centered
        IM = abel.tools.center.center_image(IM, method=origin)

    # map image into polar coordinates - much easier to slice
    # cartesian (Y, X) -> polar (Radius, Theta)
    polarIM, radial_coord, angle_coord = \
        abel.tools.polar.reproject_image_into_polar(IM, dr=dr, dt=dt)

    if inverse:
        # pseudo inverse Abel transform of the polar image, removes background
        # to enhance transition peaks
        polarIM = abel.dasch.two_point_transform(polarIM.T).T

    # more convenient 1-D coordinate arrays
    angles = angle_coord[0]  # angle coordinate
    radial = radial_coord[:, 0]  # radial coordinate

    # limit radial range of polar image, if selected
    if radial_range is not None:
        subr = np.logical_and(radial > radial_range[0],
                              radial < radial_range[1])
        polarIM = polarIM[subr]
        radial = radial[subr]

    # evaluate radial correction factor that aligns each angular slice
    radcorr = correction(polarIM.T, angles, radial, method=method)

    if smooth is not _deprecated:
        _deprecate('abel.tools.circularize.circularize_image() '
                   'argument "smooth" is deprecated, use "tol" instead.')
    else:
        smooth = len(angles) * tol**2

    # periodic spline radial correction vs angle
    spl = splrep(np.append(angles, angles[0] + 2 * np.pi),
                 np.append(radcorr, radcorr[0]), s=smooth, per=True)

    def radial_correction_function(angle):
        return splev(angle, spl)

    # apply the correction
    IMcirc = circularize(IM, radial_correction_function, ref_angle=ref_angle)

    if return_correction:
        return IMcirc, angles, radcorr, radial_correction_function
    else:
        return IMcirc


def circularize(IM, radial_correction_function, ref_angle=None):
    """
    Remap image from its distorted grid to the true cartesian grid.

    Parameters
    ----------
    IM : numpy 2D array
        Original image

    radial_correction_function : function(numpy 1D array)
        A function returning the radial correction for a given angle. It
        should accept a numpy 1D array of angles.

    ref_angle : None or float
        Reference angle at which the radial correction function is renormalized
        to unity. If ``None``, the angular average is used for renormalization.
    """
    # cartesian coordinate system
    Y, X = np.indices(IM.shape)

    row, col = IM.shape
    origin = (col // 2, row // 2)  # odd image

    # coordinates relative to center
    X -= origin[0]
    Y = origin[1] - Y   # negative values below the axis
    theta = np.arctan2(X, Y)  # referenced to vertical direction

    # radial scale factor at angle = ref_angle
    if ref_angle is None:
        factor = np.mean(radial_correction_function(theta))
    else:
        factor = radial_correction_function(ref_angle)

    # radial correction
    Xactual = X * factor / radial_correction_function(theta)
    Yactual = Y * factor / radial_correction_function(theta)

    # @DanHickstein magic
    # https://github.com/PyAbel/PyAbel/issues/186#issuecomment-275471271
    IMcirc = map_coordinates(IM, (origin[1] - Yactual, Xactual + origin[0]))

    return IMcirc


def _residual(param, radial, profile, previous):
    """ `scipy.optimize.leastsq` residuals function.

        Evaluate the difference between a radial-scaled intensity profile
        and its adjacent "previous" angular slice.

    """

    radial_scaling, amplitude = param[0], param[1]

    newradial = radial * radial_scaling
    spline_prof = UnivariateSpline(newradial, profile, s=0, ext=3)
    newprof = spline_prof(radial) * amplitude

    # residual cf adjacent slice profile
    return newprof - previous


def correction(polarIMTrans, angles, radial, method):
    r"""
    Determines a radial correction factors that align an angular slice
    radial intensity profile with its adjacent (previous) slice profile.

    Parameters
    ----------
    polarIMTrans : numpy 2D array
        Polar coordinate image, transposed :math:`(\theta, r)` so that each
        row is a single angle.

    angles : numpy 1D array
        Angle coordinates for one row of `polarIMTrans`.

    radial : numpy 1D array
        Radial coordinates for one column of `polarIMTrans`.

    method : str
        ``argmax``
            radial correction factor from position of maximum intensity.

        ``lsq``
            least-squares determine a radial correction factor that will align
            a radial intensity profile with the previous, adjacent slice.

    Returns
    -------
    radcorr : numpy 1D array
        radial correction factors for angles
    """

    if method == "argmax":
        # follow position of intensity maximum
        pkpos = []

        for ang, aslice in zip(angles, polarIMTrans):
            profile = aslice
            pkpos.append(profile.argmax())  # store index of peak position

        # radial correction factor relative to peak max in first angular slice
        radcorr = radial[pkpos[0]] / radial[pkpos]

    elif method == "lsq":
        # least-squares radially scale intensity profile matching previous slice

        # initial guess fit parameters: radial correction factor, and amplitude
        fitpar = np.array([1.0, 1.0])

        # storage for the radial correction factors
        radcorr = []
        radcorr.append(1)  # first slice nothing to compare with
        previous = polarIMTrans[0]

        for ang, aslice in zip(angles[1:], polarIMTrans[1:]):
            profile = aslice

            result = leastsq(_residual, fitpar, args=(radial, profile,
                             previous))

            radcorr.append(result[0][0])  # radial scale factor direct from lsq

            previous += _residual(result[0], radial, profile, previous)
            # This "previous" slice corresponds to the previous slice intensity
            # profile that has been re-scaled. Thus, if the next slice is
            # identical, it will be assigned a scale factor of 1.0

            # use the determined radial scale factor, and amplitude parameters
            # for the next slice
            fitpar = result[0]

    else:
        raise ValueError("method variable must be one of 'argmax' or 'lsq',"
                         " not '{}'".format(method))

    return np.asarray(radcorr)
