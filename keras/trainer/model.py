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

"""Implements the Keras Sequential model."""

from processInput import generator_input

import keras
from keras import backend as K
from keras import layers, models
from keras.utils import np_utils
from keras.backend import relu, sigmoid

from urlparse import urlparse

import tensorflow as tf
from tensorflow.python.saved_model import builder as saved_model_builder
from tensorflow.python.saved_model import utils
from tensorflow.python.saved_model import tag_constants, signature_constants
from tensorflow.python.saved_model.signature_def_utils_impl import build_signature_def, predict_signature_def
from tensorflow.contrib.session_bundle import exporter


def Conv1DModule(tensor, filters, kernel_size, strides=1):
  tensor = layers.Conv1D(filters, kernel_size, strides=strides, padding='valid', activation='relu')(tensor)
  tensor = layers.BatchNormalization()(tensor)
  return tensor

def residualBlock(inputTensor):
  deep = inputTensor
  deep = Conv1DModule(deep, 20, 5)
  deep = Conv1DModule(deep, 20, 5)

  low = inputTensor
  low = Conv1DModule(low, 20, 5)

  return layers.Concatenate(axis=1)([deep, low])


def model_fn(input_dim,
             labels_dim,
             hidden_units=[100, 70, 50, 20]):
  """Create a Keras Sequential model with layers."""
  inputs = layers.Input(shape=(input_dim,1))
  block = inputs
  block = residualBlock(block)
  # for units in hidden_units:
  #   model.add(layers.Dense(units=units,
  #                          input_dim=input_dim,
  #                          activation=relu))
  #   input_dim = units

  # Add a dense final layer with sigmoid function
  block = layers.Flatten()(block)
  block = layers.Dense(labels_dim, activation='softmax')(block)
  model = models.Model(inputs=inputs, outputs=block)
  compile_model(model)
  return model

def compile_model(model):
  model.compile(loss='categorical_crossentropy',
                optimizer='Adam',
                metrics=['accuracy'])
  return model

def to_savedmodel(model, export_path):
  """Convert the Keras HDF5 model into TensorFlow SavedModel."""

  builder = saved_model_builder.SavedModelBuilder(export_path)

  signature = predict_signature_def(inputs={'input': model.inputs[0]},
                                    outputs={'income': model.outputs[0]})

  with K.get_session() as sess:
    builder.add_meta_graph_and_variables(
        sess=sess,
        tags=[tag_constants.SERVING],
        signature_def_map={
            signature_constants.DEFAULT_SERVING_SIGNATURE_DEF_KEY: signature}
    )
    builder.save()

