"""
Code to write instance catalogs for phosim and imsim using the gcr-catalogs
galaxy catalogs.
"""
from __future__ import with_statement
import os
import copy
import subprocess
from collections import namedtuple
import gzip
import numpy as np
import h5py

from lsst.utils import getPackageDir
from lsst.sims.photUtils import PhotometricParameters
from lsst.sims.photUtils import BandpassDict
from lsst.sims.catalogs.definitions import parallelCatalogWriter
from lsst.sims.catalogs.decorators import cached, compound
from lsst.sims.catUtils.mixins import ParametrizedLightCurveMixin
from lsst.sims.catUtils.baseCatalogModels import StarObj
from lsst.sims.catUtils.exampleCatalogDefinitions import \
    PhoSimCatalogPoint, DefaultPhoSimHeaderMap
from lsst.sims.catUtils.mixins import VariabilityStars
from lsst.sims.catUtils.utils import ObservationMetaDataGenerator
from lsst.sims.utils import arcsecFromRadians, _getRotSkyPos
from . import PhoSimDESCQA, PhoSimDESCQA_AGN
from . import bulgeDESCQAObject_protoDC2 as bulgeDESCQAObject, \
    diskDESCQAObject_protoDC2 as diskDESCQAObject, \
    knotsDESCQAObject_protoDC2 as knotsDESCQAObject, \
    agnDESCQAObject_protoDC2 as agnDESCQAObject, \
    TwinklesCompoundInstanceCatalog_DC2 as twinklesDESCQACompoundObject, \
    sprinklerCompound_DC2 as sprinklerDESCQACompoundObject, \
    TwinklesCatalogZPoint_DC2 as DESCQACat_Twinkles
from . import DC2PhosimCatalogSN, SNFileDBObject
from .TwinklesClasses import twinkles_spec_map

__all__ = ['InstanceCatalogWriter', 'make_instcat_header', 'get_obs_md',
           'snphosimcat']


# Global `numpy.dtype` instance to define the types  
# in the csv files being read 
SNDTYPESR1p1 = np.dtype([('snid_in', int),
                         ('x0_in', float),
                         ('t0_in', float),
                         ('x1_in', float),
                         ('c_in', float),
                         ('z_in', float),
                         ('snra_in', float),
                         ('sndec_in', float)])

def snphosimcat(fname, tableName, obs_metadata, objectIDtype, sedRootDir,
                idColKey='snid_in', delimiter=',', dtype=SNDTYPESR1p1):
    """convenience function for writing out phosim instance catalogs for
    different SN populations in DC2 Run 1.1 that have been serialized to
    csv files.

    Parameters:
    -----------
    fname : string
        absolute path to csv file for SN population.
    tableName : string 
        table name describing the population to be decided by user choice.
    obs_metadata: instance of `lsst.sims.utils.ObservationMetaData`
	observation metadata describing the observation
    objectIDtype : int
        A unique integer identifying this class of astrophysical object as used
        in lsst.sims.catalogs.db.CatalogDBObject
    sedRootDir : string
        root directory for writing spectra corresponding to pointings. The spectra
        will be written to the directory `sedRootDir/Dynamic/`
    idColKey : string, defaults to values for Run1.1
        describes the input parameters to the database as interpreted from the
        csv file.
    delimiter : string, defaults to ','
        delimiter used in the csv file
    dtype : instance of `numpy.dtype`
        tuples describing the variables and types in the csv files.


    Returns
    -------
    returns an instance of a Phosim Instance catalogs with appropriate
    parameters set for the objects in the file and the obs_metadata.
    """
    dbobj = SNFileDBObject(fname, runtable=tableName,
                           idColKey=idColKey, dtype=dtype,
                           delimiter=delimiter)
    dbobj.raColName = 'snra_in'
    dbobj.decColName = 'sndec_in'
    dbobj.objectTypeId = objectIDtype
    cat = DC2PhosimCatalogSN(db_obj=dbobj, obs_metadata=obs_metadata)
    cat.surveyStartDate = 0.
    cat.maxz = 1.4 # increasing max redshift
    cat.maxTimeSNVisible = 150.0 # increasing for high z SN  
    cat.phoSimHeaderMap = DefaultPhoSimHeaderMap
    cat.writeSedFile = True

    # This means that the the spectra written by phosim will 
    # go to `spectra_files/Dynamic/specFileSN_* 
    # Note: you want DC2PhosimCatalogSN.sep to be part of this prefix
    # string. 
    # We can arrange for the phosim output to just read the string 
    # without directories or something else
    spectradir = os.path.join(sedRootDir, 'Dynamic')
    os.makedirs(spectradir, exist_ok=True)

    cat.sn_sedfile_prefix = os.path.join(spectradir, 'specFileSN_')
    return cat

class InstanceCatalogWriter(object):
    """
    Class to write instance catalogs for PhoSim and imSim using
    galaxies accessed via the gcr-catalogs interface.
    """
    def __init__(self, opsimdb, descqa_catalog, dither=True,
                 min_mag=10, minsource=100, proper_motion=False,
                 imsim_catalog=False, protoDC2_ra=0, protoDC2_dec=0,
                 agn_db_name=None, sprinkler=False):
        """
        Parameters
        ----------
        obsimdb: str
            OpSim db filename.
        descqa_catalog: str
            Name of the DESCQA galaxy catalog.
        dither: bool [True]
            Flag to enable the dithering included in the opsim db file.
        min_mag: float [10]
            Minimum value of the star magnitude at 500nm to include.
        minsource: int [100]
            Minimum number of objects for phosim.py to simulate a chip.
        proper_motion: bool [True]
            Flag to enable application of proper motion to stars.
        imsim_catalog: bool [False]
            Flag to write an imsim-style object catalog.
        protoDC2_ra: float [0]
            Desired RA (J2000 degrees) of protoDC2 center.
        protoDC2_dec: float [0]
            Desired Dec (J2000 degrees) of protoDC2 center.
        agn_db_name: str [None]
            Filename of the agn parameter sqlite db file.
        sprinkler: bool [False]
            Flag to enable the Sprinkler.
        """
        if not os.path.exists(opsimdb):
            raise RuntimeError('%s does not exist' % opsimdb)

        # load the data for the parametrized light
        # curve stellar variability model into a
        # global cache
        plc = ParametrizedLightCurveMixin()
        plc.load_parametrized_light_curves()

        self.descqa_catalog = descqa_catalog
        self.dither = dither
        self.min_mag = min_mag
        self.minsource = minsource
        self.proper_motion = proper_motion
        self.imsim_catalog = imsim_catalog
        self.protoDC2_ra = protoDC2_ra
        self.protoDC2_dec = protoDC2_dec

        self.phot_params = PhotometricParameters(nexp=1, exptime=30)
        self.bp_dict = BandpassDict.loadTotalBandpassesFromFiles()

        self.obs_gen = ObservationMetaDataGenerator(database=opsimdb,
                                                    driver='sqlite')

        self.star_db = StarObj(database='LSSTCATSIM',
                               host='fatboy.phys.washington.edu',
                               port=1433, driver='mssql+pymssql')

        if agn_db_name is None:
            raise IOError("Need to specify an Proto DC2 AGN database.")
        else:
            if os.path.exists(agn_db_name):
                self.agn_db_name = agn_db_name
            else:
                raise IOError("Path to Proto DC2 AGN database does not exist.")

        self.sprinkler = sprinkler

        self.instcats = get_instance_catalogs(imsim_catalog)

    def write_catalog(self, obsHistID, out_dir='.', fov=2):
        """
        Write the instance catalog for the specified obsHistID.

        Parameters
        ----------
        obsHistID: int
            ID of the desired visit.
        out_dir: str ['.']
            Output directory.  It will be created if it doesn't already exist.
        fov: float [2.]
            Field-of-view angular radius in degrees.  2 degrees will cover
            the LSST focal plane.
        """
        if not os.path.exists(out_dir):
            os.mkdir(out_dir)

        obs_md = get_obs_md(self.obs_gen, obsHistID, fov, dither=self.dither)
        # Add directory for writing the GLSN spectra to
        glsn_spectra_dir = str(os.path.join(out_dir, 'Dynamic'))
        twinkles_spec_map.subdir_map['(^specFileGLSN)'] = 'Dynamic'
        # Ensure that the directory for GLSN spectra is created
        os.makedirs(glsn_spectra_dir, exist_ok=True)

        cat_name = 'phosim_cat_%d.txt' % obsHistID
        star_name = 'star_cat_%d.txt' % obsHistID
        bright_star_name = 'bright_stars_%d.txt' % obsHistID
        gal_name = 'gal_cat_%d.txt' % obsHistID
        knots_name = 'knots_cat_%d.txt' % obsHistID
        #agn_name = 'agn_cat_%d.txt' % obshistid

	# SN Data
        snDataDir = os.path.join(getPackageDir('sims_GCRCatSimInterface'), 'data')
        sncsv_hostless_uDDF = 'uDDF_hostlessSN_trimmed.csv'
        sncsv_hostless_pDC2 = 'MainSurvey_hostlessSN_trimmed.csv'
        sncsv_hostless_pDC2hz = 'MainSurvey_hostlessSN_highz_trimmed.csv'
        sncsv_hosted_uDDF = 'uDDFHostedSNPositions_trimmed.csv'
        sncsv_hosted_pDC2 = 'MainSurveyHostedSNPositions_trimmed.csv'

        snpopcsvs = list(os.path.join(snDataDir, n) for n in 
                        [sncsv_hostless_uDDF,
                         sncsv_hostless_pDC2,
                         sncsv_hostless_pDC2hz,
                         sncsv_hosted_uDDF,
                         sncsv_hosted_pDC2])

        names = list(snpop.split('/')[-1].split('.')[0].strip('_trimmed')
                         for snpop in snpopcsvs)
        object_catalogs = [star_name, gal_name] + \
                          ['{}_cat_{}.txt'.format(x, obsHistID) for x in names]

        make_instcat_header(self.star_db, obs_md,
                            os.path.join(out_dir, cat_name),
                            imsim_catalog=self.imsim_catalog,
                            object_catalogs=object_catalogs)

        star_cat = self.instcats.StarInstCat(self.star_db, obs_metadata=obs_md)
        star_cat.min_mag = self.min_mag
        star_cat.photParams = self.phot_params
        star_cat.lsstBandpassDict = self.bp_dict
        star_cat.disable_proper_motion = not self.proper_motion

        bright_cat \
            = self.instcats.BrightStarInstCat(self.star_db, obs_metadata=obs_md,
                                              cannot_be_null=['isBright'])
        bright_cat.min_mag = self.min_mag
        bright_cat.photParams = self.phot_params
        bright_cat.lsstBandpassDict = self.bp_dict

        cat_dict = {os.path.join(out_dir, star_name): star_cat,
                    os.path.join(out_dir, bright_star_name): bright_cat}
        parallelCatalogWriter(cat_dict, chunk_size=100000, write_header=False)

        # TODO: Find a better way of checking for catalog type
        if 'knots' in self.descqa_catalog:
            knots_db =  knotsDESCQAObject(self.descqa_catalog)
            knots_db.field_ra = self.protoDC2_ra
            knots_db.field_dec = self.protoDC2_dec
            cat = self.instcats.DESCQACat(knots_db, obs_metadata=obs_md,
                                          cannot_be_null=['hasKnots'])
            cat.photParams = self.phot_params
            cat.lsstBandpassDict = self.bp_dict
            cat.write_catalog(os.path.join(out_dir, knots_name), chunk_size=100000,
                              write_header=False)
        else:
            # Creating empty knots component
            subprocess.check_call('cd %(out_dir)s; touch %(knots_name)s' % locals(), shell=True)

        if self.sprinkler is False:

            bulge_db = bulgeDESCQAObject(self.descqa_catalog)
            bulge_db.field_ra = self.protoDC2_ra
            bulge_db.field_dec = self.protoDC2_dec
            cat = self.instcats.DESCQACat(bulge_db, obs_metadata=obs_md,
                                          cannot_be_null=['hasBulge'])
            cat.write_catalog(os.path.join(out_dir, gal_name), chunk_size=100000,
                              write_header=False)
            cat.photParams = self.phot_params
            cat.lsstBandpassDict = self.bp_dict

            disk_db = diskDESCQAObject(self.descqa_catalog)
            disk_db.field_ra = self.protoDC2_ra
            disk_db.field_dec = self.protoDC2_dec
            cat = self.instcats.DESCQACat(disk_db, obs_metadata=obs_md,
                                          cannot_be_null=['hasDisk'])
            cat.write_catalog(os.path.join(out_dir, gal_name), chunk_size=100000,
                              write_mode='a', write_header=False)
            cat.photParams = self.phot_params
            cat.lsstBandpassDict = self.bp_dict

            agn_db = agnDESCQAObject(self.descqa_catalog)
            agn_db.field_ra = self.protoDC2_ra
            agn_db.field_dec = self.protoDC2_dec
            agn_db.agn_params_db = self.agn_db_name
            cat = self.instcats.DESCQACat_Agn(agn_db, obs_metadata=obs_md)
            cat.write_catalog(os.path.join(out_dir, gal_name), chunk_size=100000,
                              write_mode='a', write_header=False)
            cat.photParams = self.phot_params
            cat.lsstBandpassDict = self.bp_dict
        else:

            self.compoundGalICList = [self.instcats.DESCQACat_Bulge, self.instcats.DESCQACat_Disk,
                                      self.instcats.DESCQACat_Twinkles]
            self.compoundGalDBList = [bulgeDESCQAObject,
                                      diskDESCQAObject,
                                      agnDESCQAObject]

            gal_cat = twinklesDESCQACompoundObject(self.compoundGalICList,
                                                   self.compoundGalDBList,
                                                   obs_metadata=obs_md,
                                                   compoundDBclass=sprinklerDESCQACompoundObject,
                                                   field_ra=self.protoDC2_ra,
                                                   field_dec=self.protoDC2_dec,
                                                   agn_params_db=self.agn_db_name)

            gal_cat.use_spec_map = twinkles_spec_map
            gal_cat.sed_dir = glsn_spectra_dir
            gal_cat.photParams = self.phot_params
            gal_cat.lsstBandpassDict = self.bp_dict

            gal_cat.write_catalog(os.path.join(out_dir, gal_name), chunk_size=100000,
                                  write_header=False)
        
        # SN instance catalogs
        for i, snpop in enumerate(snpopcsvs):
            phosimcatalog = snphosimcat(snpop, tableName=names[i],
                                        sedRootDir=out_dir, obs_metadata=obs_md,
                                        objectIDtype=i+42)
            phosimcatalog.photParams = self.phot_params
            phosimcatalog.lsstBandpassDict = self.bp_dict

            snOutFile = names[i] +'_cat_{}.txt'.format(obsHistID)  
            print('writing out catalog ', snOutFile)
            phosimcatalog.write_catalog(os.path.join(out_dir, snOutFile),
                                        chunk_size=10000, write_header=False)

        if self.imsim_catalog:

            imsim_cat = 'imsim_cat_%i.txt' % obsHistID
            command = 'cd %(out_dir)s; cat %(cat_name)s %(star_name)s %(gal_name)s %(knots_name)s > %(imsim_cat)s' % locals()
            subprocess.check_call(command, shell=True)

        # gzip the object files.
        for orig_name in object_catalogs:
            full_name = os.path.join(out_dir, orig_name)
            with open(full_name, 'rb') as input_file:
                with gzip.open(full_name+'.gz', 'wb') as output_file:
                    output_file.writelines(input_file)
            os.unlink(full_name)


def make_instcat_header(star_db, obs_md, outfile, object_catalogs=(),
                        imsim_catalog=False, nsnap=1, vistime=30.,
                        minsource=100):
    """
    Write the header part of an instance catalog.

    Parameters
    ----------
    star_db: lsst.sims.catUtils.baseCatalogModels.StarObj
        InstanceCatalog object for stars.  Connects to the UW fatboy db server.
    obs_md: lsst.sims.utils.ObservationMetaData
        Observation metadata object.
    object_catalogs: sequence [()]
        Object catalog names to include in base phosim instance catalog.
        Defaults to an empty tuple.
    imsim_catalog: bool [False]
        Flag to write an imSim-style object catalog.
    nsnap: int [1]
        Number of snaps per visit.
    vistime: float [30.]
        Visit time in seconds.
    minsource: int [100]
        Minimum number of objects for phosim.py to simulate a chip.

    Returns
    -------
    lsst.sims.catUtils.exampleCatalogDefinitions.PhoSimCatalogPoint object
    """
    cat = PhoSimCatalogPoint(star_db, obs_metadata=obs_md)
    cat.phoSimHeaderMap = copy.deepcopy(DefaultPhoSimHeaderMap)
    cat.phoSimHeaderMap['nsnap'] = nsnap
    cat.phoSimHeaderMap['vistime'] = vistime
    if imsim_catalog:
        cat.phoSimHeaderMap['rawSeeing'] = ('rawSeeing', None)
        cat.phoSimHeaderMap['FWHMgeom'] = ('FWHMgeom', None)
        cat.phoSimHeaderMap['FWHMeff'] = ('FWHMeff', None)
    else:
        cat.phoSimHeaderMap['camconfig'] = 1

    with open(outfile, 'w') as output:
        cat.write_header(output)
        if not imsim_catalog:
            output.write('minsource %i\n' % minsource)
            for cat_name in object_catalogs:
                output.write('includeobj %s.gz\n' % cat_name)
    return cat


def get_obs_md(obs_gen, obsHistID, fov=2, dither=True):
    """
    Get the ObservationMetaData object for the specified obsHistID.

    Parameters
    ----------
    obs_gen: lsst.sims.catUtils.utils.ObservationMetaDataGenerator
        Object that reads the opsim db file and generates obs_md objects.
    obsHistID: int
        The ID number of the desired visit.
    fov: float [2]
        Field-of-view angular radius in degrees.  2 degrees will cover
        the LSST focal plane.
    dither: bool [True]
        Flag to apply dithering in the opsim db file.

    Returns
    -------
    lsst.sims.utils.ObservationMetaData object
    """
    obs_md = obs_gen.getObservationMetaData(obsHistID=obsHistID,
                                            boundType='circle',
                                            boundLength=fov)[0]
    if dither:
        obs_md.pointingRA \
            = np.degrees(obs_md.OpsimMetaData['descDitheredRA'])
        obs_md.pointingDec \
            = np.degrees(obs_md.OpsimMetaData['descDitheredDec'])
        obs_md.OpsimMetaData['rotTelPos'] \
            = obs_md.OpsimMetaData['descDitheredRotTelPos']
        obs_md.rotSkyPos \
            = np.degrees(_getRotSkyPos(obs_md._pointingRA, obs_md._pointingDec,
                                       obs_md, obs_md.OpsimMetaData['rotTelPos']))
    return obs_md


def get_instance_catalogs(imsim_catalog=False):





    InstCats = namedtuple('InstCats', ['StarInstCat', 'BrightStarInstCat',
                                       'DESCQACat', 'DESCQACat_Bulge',
                                       'DESCQACat_Disk', 'DESCQACat_Agn',
                                       'DESCQACat_Twinkles'])
    if imsim_catalog:
        return InstCats(MaskedPhoSimCatalogPoint_ICRS, BrightStarCatalog_ICRS,
                        PhoSimDESCQA_ICRS, DESCQACat_Bulge_ICRS, DESCQACat_Disk_ICRS,
                        DESCQACat_Agn_ICRS, DESCQACat_Twinkles_ICRS)

    return InstCats(MaskedPhoSimCatalogPoint, BrightStarCatalog,
                    PhoSimDESCQA, DESCQACat_Bulge, DESCQACat_Disk,
                    PhoSimDESCQA_AGN, DESCQACat_Twinkles)

class DESCQACat_Bulge(PhoSimDESCQA):

    cannot_be_null=['hasBulge']

class DESCQACat_Disk(PhoSimDESCQA):

    cannot_be_null=['hasDisk']

class MaskedPhoSimCatalogPoint(VariabilityStars, PhoSimCatalogPoint):
    disable_proper_motion = False
    min_mag = None
    column_outputs = ['prefix', 'uniqueId', 'raPhoSim', 'decPhoSim',
                      'maskedMagNorm', 'sedFilepath',
                      'redshift', 'gamma1', 'gamma2', 'kappa',
                      'raOffset', 'decOffset',
                      'spatialmodel', 'internalExtinctionModel',
                      'galacticExtinctionModel', 'galacticAv', 'galacticRv']
    protoDc2_half_size = 2.5*np.pi/180.

    @compound('quiescent_lsst_u', 'quiescent_lsst_g',
              'quiescent_lsst_r', 'quiescent_lsst_i',
              'quiescent_lsst_z', 'quiescent_lsst_y')
    def get_quiescentLSSTmags(self):
        return np.array([self.column_by_name('umag'),
                         self.column_by_name('gmag'),
                         self.column_by_name('rmag'),
                         self.column_by_name('imag'),
                         self.column_by_name('zmag'),
                         self.column_by_name('ymag')])

    @cached
    def get_maskedMagNorm(self):

        # What follows is a terrible hack.
        # There's a bug in CatSim such that, it won't know
        # to query for quiescent_lsst_* until after
        # the database query has been run.  Fixing that
        # bug is going to take more careful thought than
        # we have time for before Run 1.1, so I am just
        # going to request those columns now to make sure
        # they get queried for.
        self.column_by_name('quiescent_lsst_u')
        self.column_by_name('quiescent_lsst_g')
        self.column_by_name('quiescent_lsst_r')
        self.column_by_name('quiescent_lsst_i')
        self.column_by_name('quiescent_lsst_z')
        self.column_by_name('quiescent_lsst_y')

        raw_norm = self.column_by_name('phoSimMagNorm')
        if self.min_mag is None:
            return raw_norm
        return np.where(raw_norm < self.min_mag, self.min_mag, raw_norm)

    @cached
    def get_inProtoDc2(self):
        ra_values = self.column_by_name('raPhoSim')
        ra = np.where(ra_values < np.pi, ra_values, ra_values - 2.*np.pi)
        dec = self.column_by_name('decPhoSim')
        return np.where((ra > -self.protoDc2_half_size) &
                        (ra < self.protoDc2_half_size) &
                        (dec > -self.protoDc2_half_size) &
                        (dec < self.protoDc2_half_size), 1, None)

    def column_by_name(self, colname):
        if (self.disable_proper_motion and
            colname in ('properMotionRa', 'properMotionDec',
                        'radialVelocity', 'parallax')):
            return np.zeros(len(self.column_by_name('raJ2000')), dtype=np.float)
        return super(MaskedPhoSimCatalogPoint, self).column_by_name(colname)


class BrightStarCatalog(PhoSimCatalogPoint):
    min_mag = None

    @cached
    def get_isBright(self):
        raw_norm = self.column_by_name('phoSimMagNorm')
        return np.where(raw_norm < self.min_mag, raw_norm, None)

class PhoSimDESCQA_ICRS(PhoSimDESCQA):
    catalog_type = 'phoSim_catalog_DESCQA_ICRS'

    column_outputs = ['prefix', 'uniqueId', 'raJ2000', 'decJ2000',
                      'phoSimMagNorm', 'sedFilepath',
                      'redshift', 'gamma1', 'gamma2', 'kappa',
                      'raOffset', 'decOffset',
                      'spatialmodel', 'majorAxis', 'minorAxis',
                      'positionAngle', 'sindex',
                      'internalExtinctionModel', 'internalAv', 'internalRv',
                      'galacticExtinctionModel', 'galacticAv', 'galacticRv',]

    transformations = {'raJ2000': np.degrees,
                       'decJ2000': np.degrees,
                       'positionAngle': np.degrees,
                       'majorAxis': arcsecFromRadians,
                       'minorAxis': arcsecFromRadians}

class DESCQACat_Disk_ICRS(PhoSimDESCQA_ICRS):

    cannot_be_null=['hasDisk']

class DESCQACat_Bulge_ICRS(PhoSimDESCQA_ICRS):

    cannot_be_null=['hasBulge']

class DESCQACat_Agn_ICRS(PhoSimDESCQA_AGN):
    catalog_type = 'phoSim_catalog_DESCQA_AGN_ICRS'

    column_outputs = ['prefix', 'uniqueId', 'raJ2000', 'decJ2000',
                      'phoSimMagNorm', 'sedFilepath',
                      'redshift', 'gamma1', 'gamma2', 'kappa',
                      'raOffset', 'decOffset',
                      'spatialmodel',
                      'positionAngle',
                      'internalExtinctionModel',
                      'galacticExtinctionModel', 'galacticAv', 'galacticRv',]

    transformations = {'raJ2000': np.degrees,
                       'decJ2000': np.degrees,
                       'positionAngle': np.degrees}

class DESCQACat_Twinkles_ICRS(DESCQACat_Twinkles):
    catalog_type = 'phoSim_catalog_DESCQA_Twinkles_ICRS'

    column_outputs = ['prefix', 'uniqueId', 'raJ2000', 'decJ2000',
                      'phoSimMagNorm', 'sedFilepath',
                      'redshift', 'gamma1', 'gamma2', 'kappa',
                      'raOffset', 'decOffset',
                      'spatialmodel',
                      'positionAngle',
                      'internalExtinctionModel',
                      'galacticExtinctionModel', 'galacticAv', 'galacticRv',]

    transformations = {'raJ2000': np.degrees,
                       'decJ2000': np.degrees,
                       'positionAngle': np.degrees}


class MaskedPhoSimCatalogPoint_ICRS(MaskedPhoSimCatalogPoint):
    catalog_type = 'masked_phoSim_catalog_point_ICRS'

    column_outputs = ['prefix', 'uniqueId', 'raJ2000', 'decJ2000',
                      'maskedMagNorm', 'sedFilepath',
                      'redshift', 'gamma1', 'gamma2', 'kappa',
                      'raOffset', 'decOffset',
                      'spatialmodel',
                      'internalExtinctionModel',
                      'galacticExtinctionModel', 'galacticAv', 'galacticRv',]

    transformations = {'raJ2000': np.degrees,
                       'decJ2000': np.degrees}

class BrightStarCatalog_ICRS(BrightStarCatalog):
    catalog_type = 'bright_star_catalog_point_ICRS'

    column_outputs = ['prefix', 'uniqueId', 'raJ2000', 'decJ2000',
                      'phoSimMagNorm', 'sedFilepath',
                      'redshift', 'gamma1', 'gamma2', 'kappa',
                      'raOffset', 'decOffset',
                      'spatialmodel',
                      'internalExtinctionModel',
                      'galacticExtinctionModel', 'galacticAv', 'galacticRv',]

    transformations = {'raJ2000': np.degrees,
                       'decJ2000': np.degrees}
