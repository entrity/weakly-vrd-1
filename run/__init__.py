import sys, os
sys.path.insert(0, os.path.realpath(os.path.join(__file__,'../..')))
import numpy as np
import torch
import torch.utils.data as dat
assert torch.__version__.startswith('0.4'), 'wanted version 0.4, got %s' % torch.__version__
import os, datetime as dt
import torch.nn as nn
from classifier.generic_solver import GenericSolver as Solver
from optparse import OptionParser
import datetime

import pdb

import util.logger as logger
import dataset.dataset as dset
import dataset.zeroshot as zeroshot

DEFAULT_DATAROOT = os.path.join(os.path.dirname(__file__), '..', 'data/vrd-dataset')
DEFAULT_LOGDIR   = os.path.join(os.path.dirname(__file__), '..', 'log')
NUM_WORKERS = 4

# Parse command line args
parser = OptionParser()
parser.add_option('--data', dest='dataroot', default=DEFAULT_DATAROOT)
parser.add_option('--lr', dest='lr', default=0.001, type="float")
parser.add_option('--bs', dest='batch_size', default=32, type="int")
parser.add_option('--tbs', dest='test_batch_size', default=32, type="int", help="for cases where memory is a bigger constraint on the test set")
parser.add_option('--ep', dest='num_epochs', default=30, type="int")
parser.add_option('-N', dest='train_size', default=None, type="int")
parser.add_option('--val', dest='val', default=None, type="float", help="percentage of the primary test set to use (used as validation; remainder is unused)")
parser.add_option('--noval', action='store_false', default=True, dest='do_validation')
parser.add_option('--cpu', action='store_false', default=True, dest='cuda')
parser.add_option('--log', '--logfile', dest='logfile', default=None)
parser.add_option('--geom', dest='geometry', default='1000 2000 2000 70')
parser.add_option('--sched', dest='no_scheduler', default=True, action='store_false')
parser.add_option('--patience', dest='patience', default=10, type="int")
parser.add_option('--test_every', dest='test_every', default=None, type='int')
parser.add_option('--print_every', dest='print_every', default=None, type='int')
parser.add_option('--save', dest='save_every', default=None, type='int')
parser.add_option('--end-save', dest='save_at_end', default=False, action='store_true')
parser.add_option('--nosave', dest='save_best', default=True, action='store_false')
parser.add_option('--outdir', '--dir', '--logdir', dest='outdir', default='log', help="Used for saving logs, checkpoints, etc.")
parser.add_option('--noprefix', dest='no_prefix', default=False, action='store_true', help='If true, don\'t include name in outdir')
parser.add_option('--nosplitzs', dest='split_zeroshot', default=True, action='store_false')
parser.add_option('--recall', dest='recall_every', default=0, type='int')
parser.add_option('--load', dest='load', default=None, help='Model or state_dict to load')
parser.add_option('-s', help='Save initialized, untrained model', dest='save_initialized_model')

class Runner(object):

	def __init__(self):
		# Initialize
		self.model = None
		self.optimizer = None
		self.scheduler = None
		self.solver = None
		self.opts, self.args = parser.parse_args()
		opts = self.opts
		timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M')
		self.name = ('%s N-%d ep-%d lr-%f geom-%s' % (timestamp, opts.train_size or 0, opts.num_epochs, opts.lr, opts.geometry))
		# Set logger
		if self.opts.no_prefix:
			log_name = self.name + '.log'
		else:
			log_name = 'out.log'
			self.opts.outdir = os.path.join(self.opts.outdir or '', self.name)
		if self.opts.logfile:
			logger.Logger(self.opts.logfile)
		elif self.opts.outdir:
			logger.Logger(self.opts.outdir, log_name)
		print(sys.argv)
		print(opts)
		print('PID %d' % (os.getpid(),))

	def setup(self):
		print('Init...')
		self.setup_model()
		print('Initializing dataset(s)...')
		self.setup_data()
		self.setup_opt()

	def setup_model(self):
		# Define model
		if self.model == None:
			if self.opts.load:
				self._load_model()
			elif self.model == None:
				self.model = self._build_model()

	def _load_model(self):
		if self.opts.load:
			print('Loading model')
			from_file = torch.load(self.opts.load)
			if isinstance(from_file, torch.nn.Module):
				self.model = from_file
			else:
				self.model = self._build_model()
				self.model.load_state_dict(from_file)

	def _build_model(self):
		print('Building model')
		layer_widths = [int(x) for x in self.opts.geometry.split(' ')]
		print('Geometry: %s' % (' '.join((str(x) for x in layer_widths))))
		def model_generator(layer_widths, is_batch_gt_1):
			for i in range(1, len(layer_widths)):
				yield nn.Linear(layer_widths[i-1], layer_widths[i])
				if i < len(layer_widths) - 1: # All except the last
					yield nn.Dropout()
					yield nn.BatchNorm1d(layer_widths[i])
					yield nn.ReLU()
		layers = list(model_generator(layer_widths, self.opts.train_size == 1))
		self.model = nn.Sequential(*layers).double()
		if self.opts.save_initialized_model:
			print('Saving initialized model to %s' % (self.opts.save_initialized_model,))
			torch.save(self.model, self.opts.save_initialized_model)
		return self.model

	def setup_opt(self):
		# Define optimizer, scheduler, solver
		if self.optimizer == None:
			print('Building optimizer...')
			self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.opts.lr)
		if self.scheduler == None:
			print('Building scheduler...')
			self.scheduler = None if self.opts.no_scheduler else torch.optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, verbose=True, patience=self.opts.patience)
		if self.solver == None:
			print('Building solver...')
			self.solver = Solver(self.model, self.optimizer, verbose=True, scheduler=self.scheduler, **self.opts.__dict__)

	def setup_data(self):
		# Initialize trainset
		dataroot = self.opts.dataroot
		_trainset = dset.Dataset(dataroot, 'train', pairs='annotated')
		self.trainloader = dat.DataLoader(_trainset, batch_size=self.opts.batch_size, shuffle=True, num_workers=4)

		# Use subset of train data
		if self.opts.train_size: # if --N: override the __len__ method of the dataset so that only the first N items will be used
			def train_size(unused): return self.opts.train_size
			_trainset.__class__.__len__ = train_size

		# Initialize testset
		if self.opts.do_validation: # Defatult True
			_testset = dset.Dataset(dataroot, 'test', pairs='annotated')
			if self.opts.split_zeroshot: # Split testset into seen and zeroshot sets
				test_sets = zeroshot.Splitter(_trainset, _testset).split()
				self.testloaders = [dat.DataLoader(data, batch_size=len(data), num_workers=NUM_WORKERS) for data in test_sets]
			else: # Use a single (unified) testset
				testdata = dat.DataLoader(_testset, batch_size=len(_testset), num_workers=NUM_WORKERS)
				self.testloaders = [testdata]
			if self.opts.val: # Use only x percent of the primary testset as validation (and don't use the rest at this time)
				dataset = self.testloaders[0].dataset
				n = int(len(dataset) * self.opts.val)
				sampler = dat.SubsetRandomSampler(torch.arange(n))
				self.testloaders[0] = dat.DataLoader(dataset, batch_size=n, sampler=sampler, num_workers=NUM_WORKERS)
		else: # if --noval
			self.testloaders = []

	def train(self):
		print('Model file:', self.model.__file__)
		print('Trainset file:', self.trainloader.dataset.__file__)
		print('Trainloader file:', self.trainloader.__file__)
		for testloader in self.testloaders:
			print('Testset file:', testloader.dataset.__file__)
			print('Testloader file:', testloader.__file__)	
		print('Training...')
		self.solver.train(self.trainloader, *self.testloaders)
