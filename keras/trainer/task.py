# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""This code implements a Feed forward neural network using Keras API."""

import argparse
import glob
import json
import os
import numpy as np
import keras
from keras.models import load_model
import model
from processInput import INPUT_SIZE, OUTPUT_SIZE
from tensorflow.python.lib.io import file_io


# CHUNK_SIZE specifies the number of lines
# to read in case the file is very large
FILE_PATH = 'checkpoint.{epoch:02d}.hdf5'
MODEL_SAVE_PATH = 'census.hdf5'

def copy_file_to(src, dest):
  with file_io.FileIO(src, mode='r') as input_f:
    with file_io.FileIO(dest, mode='w') as output_f:
        output_f.write(input_f.read())

def copy_file_to_gcs(job_dir, file_path):
  copy_file_to(file_path, os.path.join(job_dir, file_path))

class ContinuousEval(keras.callbacks.Callback):
  """Continuous eval callback to evaluate the checkpoint once
     every so many epochs.
  """

  def __init__(self,
               eval_frequency,
               eval_files,
               eval_batch_size,
               job_dir,
               steps=100):
    self.eval_files = eval_files
    self.eval_frequency = eval_frequency
    self.job_dir = job_dir
    self.steps = steps
    self.eval_batch_size = eval_batch_size

  def on_epoch_begin(self, epoch, logs={}):
    if epoch > 0 and epoch % self.eval_frequency == 0:
      # Unhappy hack to work around h5py not being able to write to GCS.
      # Force snapshots and saves to local filesystem, then copy them over to GCS.
      model_path_glob = 'checkpoint.*'
      if not self.job_dir.startswith("gs://"):
        model_path_glob = os.path.join(self.job_dir, model_path_glob)
      checkpoints = glob.glob(model_path_glob)
      if len(checkpoints) > 0:
        checkpoints.sort()
        dlModel = load_model(checkpoints[-1])
        dlModel = model.compile_model(dlModel)
        metrics = dlModel.evaluate_generator(
            model.generator_input(self.eval_files, chunk_size=self.eval_batch_size),
            steps=self.steps)
        print '\nEvaluation epoch[{}] metrics: {} [{}]'.format(
            epoch, dlModel.metrics_names, metrics)
        if self.job_dir.startswith("gs://"):
          copy_file_to_gcs(self.job_dir, checkpoints[-1])
      else:
        print '\nEvaluation epoch[{}] (no checkpoints found)'.format(epoch)

def dispatch(train_files,
             validate_files,
             eval_files,
             job_dir,
             train_steps,
             eval_steps,
             validate_steps,
             train_batch_size,
             early_stop_patience,
             eval_batch_size,
             eval_frequency,
             first_layer_size,
             num_layers,
             scale_factor,
             eval_num_epochs,
             num_epochs,
             checkpoint_epochs,
             verbose):
  # Get the configuration data from the environment variable.
  env = json.loads(os.environ.get('TF_CONFIG', '{}'))
  taskInfo = env.get('task')
  if taskInfo:
      trial = taskInfo.get('trial', '')
      if trial:
          job_dir = os.path.join(output_path, trial)

  dlModel = model.model_fn(INPUT_SIZE, OUTPUT_SIZE)

  try:
    os.makedirs(job_dir)
  except:
    pass

  # Unhappy hack to work around h5py not being able to write to GCS.
  # Force snapshots and saves to local filesystem, then copy them over to GCS.
  checkpoint_path = FILE_PATH
  if not job_dir.startswith("gs://"):
    checkpoint_path = os.path.join(job_dir, checkpoint_path)

  # Model checkpoint callback
  checkpoint = keras.callbacks.ModelCheckpoint(
      checkpoint_path,
      monitor='val_loss',
      verbose=verbose,
      period=checkpoint_epochs,
      mode='max')

  # Continuous eval callback
  evaluation = ContinuousEval(eval_frequency=eval_frequency,
                              eval_files=eval_files,
                              job_dir=job_dir,
                              eval_batch_size=eval_batch_size,
                              steps=train_steps)

  # Tensorboard logs callback
  tblog = keras.callbacks.TensorBoard(
      log_dir=os.path.join(job_dir, 'logs'),
      histogram_freq=0,
      write_graph=True,
      embeddings_freq=0)

  earlyStop = keras.callbacks.EarlyStopping(patience=early_stop_patience)
  
  callbacks=[checkpoint, evaluation, tblog, earlyStop]

  dlModel.fit_generator(
      model.generator_input(train_files, chunk_size=train_batch_size),
      validation_data=model.generator_input(validate_files, chunk_size=eval_batch_size),
      validation_steps=validate_steps,
      steps_per_epoch=train_steps,
      epochs=num_epochs,
      verbose=verbose,
      callbacks=callbacks)

  # Unhappy hack to work around h5py not being able to write to GCS.
  # Force snapshots and saves to local filesystem, then copy them over to GCS.
  if job_dir.startswith("gs://"):
    dlModel.save(MODEL_SAVE_PATH)
    copy_file_to_gcs(job_dir, MODEL_SAVE_PATH)
  else:
    dlModel.save(os.path.join(job_dir, MODEL_SAVE_PATH))

  # Convert the Keras model to TensorFlow SavedModel
  model.to_savedmodel(dlModel, os.path.join(job_dir, 'export'))

if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument('--train-files',
                      required=True,
                      type=str,
                      help='Training files local or GCS', nargs='+')
  parser.add_argument('--validate-files',
                      required=True,
                      type=str,
                      help='Evaluation files local or GCS', nargs='+')
  parser.add_argument('--eval-files',
                      required=True,
                      type=str,
                      help='Evaluation files local or GCS', nargs='+')
  parser.add_argument('--job-dir',
                      required=True,
                      type=str,
                      help='GCS or local dir to write checkpoints and export model')
  parser.add_argument('--train-steps',
                      type=int,
                      default=100,
                      help="""\
                       Maximum number of training steps to perform
                       Training steps are in the units of training-batch-size.
                       So if train-steps is 500 and train-batch-size if 100 then
                       at most 500 * 100 training instances will be used to train.
                      """)
  parser.add_argument('--validate-steps',
                      help='Number of steps to run evalution for at each checkpoint',
                      default=30,
                      type=int)
  parser.add_argument('--eval-steps',
                      help='Number of steps to run evalution for at each checkpoint',
                      default=100,
                      type=int)
  parser.add_argument('--train-batch-size',
                      type=int,
                      default=100,
                      help='Batch size for training steps')
  parser.add_argument('--eval-batch-size',
                      type=int,
                      default=100,
                      help='Batch size for evaluation steps')
  parser.add_argument('--early-stop-patience',
                      type=int,
                      default=2,
                      help='Patience for early stop')
  parser.add_argument('--eval-frequency',
                      type=int,
                      default=10,
                      help='Perform one evaluation per n epochs')
  parser.add_argument('--first-layer-size',
                     type=int,
                     default=256,
                     help='Number of nodes in the first layer of DNN')
  parser.add_argument('--num-layers',
                     type=int,
                     default=2,
                     help='Number of layers in DNN')
  parser.add_argument('--scale-factor',
                     type=float,
                     default=0.25,
                     help="""\
                      Rate of decay size of layer for Deep Neural Net.
                      max(2, int(first_layer_size * scale_factor**i)) \
                      """)
  parser.add_argument('--eval-num-epochs',
                     type=int,
                     default=1,
                     help='Number of epochs during evaluation')
  parser.add_argument('--num-epochs',
                      type=int,
                      default=20,
                      help='Maximum number of epochs on which to train')
  parser.add_argument('--verbose',
                      type=int,
                      default=2,
                      help='How much log to print')
  parser.add_argument('--checkpoint-epochs',
                      type=int,
                      default=5,
                      help='Checkpoint per n training epochs')
  parse_args, unknown = parser.parse_known_args()

  dispatch(**parse_args.__dict__)
