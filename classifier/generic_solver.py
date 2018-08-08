import torch
import torch.nn as nn
import time
import pdb

class GenericSolver:
	def __init__(self, model, optimizer, **opts):
		self.model       = model
		self.optimizer   = optimizer
		self.scheduler   = opts.get('scheduler', None)
		self.cuda        = opts.get('cuda', True)
		self.supervision = opts.get('supervision', WEAK)
		self.verbose     = opts.get('verbose', False)
		self.num_epochs  = opts.get('num_epochs', 9)
		self.print_every = opts.get('print_every', 20)
		self.test_every  = opts.get('test_every', 80)
		self.loss_fn     = opts.get('loss', nn.CrossEntropyLoss())
		self.dtype       = opts.get('dtype', torch.double)
	
	# @arg trainloader should be a Dataloader
	# @arg testloaders should be a Dataloaders
	def train(self, trainloader, *testloaders):
		self.num_iterations = self.num_epochs * len(trainloader)
		self.loss_history = torch.Tensor(self.num_iterations)

		if self.cuda: self.model.cuda()

		self.debug()
		print('%20s %s' % ('num_epochs', self.num_epochs,))
		print('%20s %s' % ('num_batches', len(trainloader),))
		print('%20s %s' % ('batch_size', trainloader.batch_size,))

		self.iteration = 0
		for self.epoch in range(self.num_epochs):
			for batch_i, batch in enumerate(trainloader):
				tic = time.time()
				loss = self._train_step(batch['X'], batch['y'])
				toc = (time.time() - tic) / len(batch['y'])
				if self.verbose and batch_i % self.print_every == 0:
					self._print(loss)
				if self.scheduler and testloaders is None:
					self.scheduler.step(loss)
				if testloaders and batch_i % self.test_every == 0:
					self._test(testloaders)
				self.iteration += 1
	
	def _train_step(self, batch_X, batch_Y):
		self.model.train()
		self.optimizer.zero_grad()
		loss = self.loss_history[self.iteration] = self._calc_loss(batch_X, batch_Y)
		loss.backward()
		self.optimizer.step()
		return self.loss_history[self.iteration]

	def _test(self, testloaders):
		self.model.eval()
		for testloader in testloaders:
			for testbatch in testloader:
				loss = self._calc_loss(testbatch['X'], testbatch['y'])
				self._print(loss, testloader.dataset.name or 'TEST')

	def _calc_loss(self, batch_X, batch_Y):
		if self.cuda:
			batch_X = batch_X.cuda()
			batch_Y = batch_Y.cuda()
		self.prediction = self.model(batch_X)
		correct_predictions = torch.sum( torch.argmax(self.prediction, 1) == batch_Y.transpose(1,0) ).item()
		self.acc = correct_predictions / float(batch_X.shape[0])
		return self.loss_fn( self.prediction, batch_Y.reshape(-1) )

	def _print(self, loss, dataname='TRAIN'):
		print('%12s (ep %3d: %5d/%d) loss %e\tacc %.3f' % (dataname, self.epoch, self.iteration, self.num_iterations, loss, self.acc))

	def debug(self):
		print('%20s %s' % ('optimizer', str(self.optimizer),))
		print('%20s %s' % ('model', str(self.model),))
		print('%20s %s' % ('cuda', self.cuda,))
		print('%20s %s' % ('supervision', self.supervision,))
		print('%20s %s' % ('verbose', self.verbose,))
		print('%20s %d' % ('num_epochs', self.num_epochs,))
		print('%20s %d' % ('print_every', self.print_every,))
		print('%20s %d' % ('test_every', self.test_every,))
		print('%20s %s' % ('loss_fn', str(self.loss_fn),))
		print('%20s %s' % ('dtype', self.dtype,))


WEAK = 'weak'
FULL = 'full'
