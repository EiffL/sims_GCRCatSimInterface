#!/usr/bin/env python
import argparse
from desc.sims.GCRCatSimInterface import *

parser = argparse.ArgumentParser(description='Instance catalog generator')
parser.add_argument('--db', type=str,
                    help='path to the OpSim database to query')
parser.add_argument('--agn_db_name', type=str,
                    help='File of AGN parameters generated by create_agn_db.py')
parser.add_argument('--descqa_catalog', type=str, default='protoDC2',
                    help='the desired DESCQA catalog')
parser.add_argument('--out_dir', type=str, default='.',
                    help='directory where output will be written')
parser.add_argument('--ids', type=int, nargs='+',
                    default=None,
                    help='obsHistID to generate InstanceCatalog for (a list)')
parser.add_argument('--disable_dithering', default=False,
                    action='store_true',
                    help='flag to disable dithering')
parser.add_argument('--min_mag', type=float, default=10.0,
                    help='the minimum magintude for stars')
parser.add_argument('--fov', type=float, default=2.0,
                    help='field of view radius in degrees')
parser.add_argument('--enable_proper_motion', default=False,
                    action='store_true',
                    help='flag to enable proper motion')
parser.add_argument('--minsource', type=int, default=100,
                    help='mininum #objects in a trimmed instance catalog')
parser.add_argument('--imsim_catalog', default=False, action='store_true',
                    help='flag to produce object catalog for imSim')
parser.add_argument('--protoDC2_ra', type=float, default=0,
                    help='RA (J2000 degrees) of the new protoDC2 center')
parser.add_argument('--protoDC2_dec', type=float, default=0,
                    help='Dec (J2000 degrees) of the new protoDC2 center')
parser.add_argument('--enable_sprinkler', default=False, action='store_true',
                    help='flag to enable the sprinkler')
args = parser.parse_args()

instcat_writer = InstanceCatalogWriter(args.db, args.descqa_catalog,
                                       dither=not args.disable_dithering,
                                       min_mag=args.min_mag,
                                       minsource=args.minsource,
                                       proper_motion=args.enable_proper_motion,
                                       imsim_catalog=args.imsim_catalog,
                                       protoDC2_ra=args.protoDC2_ra,
                                       protoDC2_dec=args.protoDC2_dec,
                                       agn_db_name=args.agn_db_name,
                                       sprinkler=args.enable_sprinkler)

for obsHistID in args.ids:
    instcat_writer.write_catalog(obsHistID, out_dir=args.out_dir, fov=args.fov)
