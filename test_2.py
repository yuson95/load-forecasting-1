# -*- coding: utf-8 -*-
"""
Created on Tue Oct 30 15:48:17 2018

@author: leoliu
"""
'''
import start
'''
import abc  # abstract base class
import os.path
import sys
sys.path.append('C:\\Users\\leoliu\\Anaconda3\\envs\\python3.6\\lib\\site-packages\\tensorflow')
sys.path.append(os.path.realpath(os.path.dirname(os.path.realpath(__file__))))
#print(sys.path)
import datetime as dt
import numpy as np
from numpy.lib.stride_tricks import as_strided
import pandas as pd
import math
from keras.models import Sequential, load_model
from keras.models import Model as Functional_model
from keras.layers import Dense, Dropout, Convolution1D, Flatten, Input, merge, Activation
from keras.optimizers import SGD
from keras.callbacks import CSVLogger, EarlyStopping, TensorBoard, ModelCheckpoint
import tensorflow as tf
from keras import backend as K
from keras import metrics
import utils.utils as utils
import config as config
from keras.layers import LSTM
from ann_abstr import Ann_model
from metrics import mean_neg_log_loss_parametric, mean_neg_log_loss_discrete
from lstm import Prob_LSTM
import matplotlib.pyplot as plt
'''
import end
'''

'''
define all parameters start
'''

prefix = 'lstm_'
model_identifier='ukdale_1'
dataset_identifier='ukdale_1'
forecast_horizon_mins = 5
# forecast_horizon 1 day or 5 mins
granularity_s = 300
#forecast for every 5 mins
look_back_mins = 60
hidden_neurons=(40, 40, 40)
sliding_window_width = int(dt.timedelta(minutes=look_back_mins).total_seconds() / granularity_s)
#回看多少个数据
nb_input_neurons = sliding_window_width
working_directory=config.working_directory
trained_models_folder=config.trained_models_folder
preprocessed_datasets_folder=config.preprocessed_datasets_folder

forecast_type='watthours'

learning_rate=0.1
validation_split=0.0
batch_size = 2000
#batch_size will be changed(not global) in generating an output of type [0,0,...,Pt,...,0,0] to be compared against the pdf output from the model
nb_epoch=100
epochs = nb_epoch
verbose=0
patience=50
dropout=0.2
use_cal_vars=True
activation='sigmoid'

pdf_sample_points_min=0.0
pdf_sample_points_max=1.0
pdf_resolution=5000.0

loss_func = mean_neg_log_loss_discrete
pdf_granularity = (pdf_sample_points_max - pdf_sample_points_min) / pdf_resolution
pdf_sample_points = np.linspace(pdf_sample_points_min, pdf_sample_points_max, pdf_resolution)

train_cv_test_split=(0.8, 0.1, 0.1)
cleanse=False
'''
define all parameters end
'''

'''
define all functions start
'''
def generate_dataset_filename(dataset_identifier):
    """
	Generate the dataset's filename corresponding to the model's granularity
	"""
    return utils.generate_dataset_filename(dataset_identifier, granularity_s)

def open_dataset_file(filename, preprocessed_datasets_folder=config.preprocessed_datasets_folder):
    """
	"""
    return utils.open_dataset_file(filename, working_directory, preprocessed_datasets_folder)

def scale(values, scaling_factor):
    """
	Scales the values by the model's scaling_factor
	"""
    return values / (scaling_factor * 1.0)

def generate_input_data(lagged_vals, t0, scaling_factor):
    """
    Prepare (normalize) input data for forecasting
    """
    X = scale(lagged_vals, scaling_factor)
    X = X.reshape((X.shape[0], X.shape[1], 1))

    if use_cal_vars:

        minutes = t0.minute
        # Normalized values
        minutes = minutes / 60.0
        hours = t0.hour
        hours = hours / 24.0
        day = t0.weekday
        day = day / 7.0
        month = t0.month
        month = month / 12.0
    
        minsaux = np.zeros(X.shape)
        hoursaux = np.zeros(X.shape)
        daysaux = np.zeros(X.shape)
        monthsaux = np.zeros(X.shape)
    
        for i_sample in range(len(t0)):
            for i_timestamp in range(lagged_vals.shape[1]):
                minsaux[i_sample][i_timestamp][0] = minutes[i_sample]
                hoursaux[i_sample][i_timestamp][0] = (hours[i_sample])
                daysaux[i_sample][i_timestamp][0] = (day[i_sample])
                monthsaux[i_sample][i_timestamp][0] = (month[i_sample])
    
        minutes = minsaux
        hours = hoursaux
        day = daysaux
        month = monthsaux
    
        if activation == 'tanh':
            minutes = minutes * 2.0 - 1  # scale to [-1,1]
            hours = hours * 2.0 - 1
            day = day * 2.0 - 1
            month = month * 2.0 - 1
    
        X = np.concatenate((X, minutes, hours, day, month), axis=2)
        
    return X

def generate_output_data(ground_truth):
        """
        Generates an output of type [0,0,...,Pt,...,0,0] to be compared against the pdf output from the model
        """
        nb_sample_points = len(pdf_sample_points)
        batch_size = len(ground_truth)
        y = np.zeros((batch_size, nb_sample_points))
        
        pdf_sample_points_grid = pdf_sample_points.reshape((1, nb_sample_points))
        pdf_sample_points_grid = np.repeat(pdf_sample_points_grid, batch_size, axis=0)

        ground_truth_grid = ground_truth.reshape((batch_size, 1))
        ground_truth_grid = np.repeat(ground_truth_grid, nb_sample_points, axis=1)

        rows_idx = np.arange(0, batch_size)
        cols_idx = np.argmin(np.abs(pdf_sample_points_grid - ground_truth_grid), axis=1)
        y[rows_idx, cols_idx] = 1.0

        return y

def generate_training_data_lstm(dataset, train_cv_test_split=train_cv_test_split, cleanse=False):
    """
	From Pandas DataFrame (timestep, watthour) generate all valid training examples and split respectivly; X and y
	are scaled to model scale.
	"""
    # load raw data, df for dataframe
    ds = dataset.copy(deep=True)
    ds.watthour = np.nan_to_num(ds.watthour.values)
    # 5mins load (288 loads for each day)

    scaling_factor =np.max(np.array(ds.watthour))

    nb_forecast_steps = int(dt.timedelta(minutes=forecast_horizon_mins).total_seconds() / granularity_s)

    nb_examples = len(np.array(ds.watthour)) - nb_forecast_steps - sliding_window_width

    #history_offset = sliding_window_width + nb_forecast_steps - 1

    #lagged_vals = np.array(list_5mins_load[-history_offset:])

    s = np.array(ds.watthour).itemsize

    lagged_vals = as_strided(np.array(ds.watthour), shape=(nb_examples, sliding_window_width), strides=(s, s))

    if sliding_window_width != 0:
            t0 = ds.index[sliding_window_width - 1:-nb_forecast_steps - 1]
    else:
            t0 = ds.index[:-nb_forecast_steps]

    if forecast_type == 'watthours':
        s = np.array(ds.watthour).itemsize
        watthour_intervals = as_strided(np.array(ds.watthour)[sliding_window_width:], shape=(nb_examples, nb_forecast_steps), strides=(s, s))
                # print "fc_hor", watthour_intervals[2]
                # print "last_elem_of_sum", watthour_intervals[:,-1]
                # print "P_t1", ds.loc[t0+pd.Timedelta(minutes=self.forecast_horizon_mins)].watthour.values
        mask = np.unique(np.where(watthour_intervals != 0.0)[0])  # np.where returns indices for nonzero values as [xi][yi]; take only unique row indices
        #?????找出watthour_intervals中不是0的数的行数
        ground_truth = np.sum(watthour_intervals,axis=-1) * granularity_s / 3600  # integrate watthour over forecast horizon to get total energy in Wh
        #?????    
        # print y.shape
    elif forecast_type == 'watts':
        ground_truth = ds.watthour.values[sliding_window_width + nb_forecast_steps - 1:-1]
                #mask = np.array(np.where(y != 0.0)).reshape((-1))
            # print 'gt', ground_truth
            # print "P_t1", ds.loc[t0+pd.Timedelta(minutes=self.forecast_horizon_mins)].watthour.values
    else:
        print('Unsupported forecast type. Please define forecast type as either \'watts\' or \'watthours\'.')

            # print(">>ground truth", ground_truth)
            # ground truth real values
    ground_truth = ground_truth.reshape(-1, 1)

    ground_truth = (ground_truth/scaling_factor)
    #????????为什么这里ground_truth没有大于1的？----因为前面*granularity_s/3600,另外这里跟源代码不一样

    # generate_input_data
    X = generate_input_data(lagged_vals, t0, scaling_factor)

    # y is an vector with normalized energy consumption within the time interval
    y = generate_output_data(ground_truth)
    
    # print(">>ground truth", ground_truth)
    # print(">>output", y)

    if cleanse:
        ground_truth = ground_truth[mask]
        y = y[mask]  # cleansing the data leads to extreme performance drop
        X = X[mask, :]
        t0 = t0[mask]

    val_idx = int(len(y) * train_cv_test_split[0])
    test_idx = int(len(y) * (train_cv_test_split[0] + train_cv_test_split[1]))

    y_train = y[0:val_idx]
    X_train = X[0:val_idx, :]
    ground_truth_train = ground_truth[0:val_idx]
    t0_train = t0[0:val_idx]

    y_val = y[val_idx:test_idx]
    t0_val = t0[val_idx:test_idx]
    ground_truth_val = ground_truth[val_idx:test_idx]
    X_val = X[val_idx:test_idx, :]

    y_test = y[test_idx:]
    t0_test = t0[test_idx:]
    ground_truth_test = ground_truth[test_idx:]
    X_test = X[test_idx:, :]

    return (X_train, y_train, ground_truth_train, t0_train), \
           (X_val, y_val, ground_truth_val, t0_val), \
           (X_test, y_test, ground_truth_test, t0_test), \
           scaling_factor

def generate_model():
    """
    Generate the neural network model
    Define the model's architecture and the implemented functions
    """
    # Size of input layer
    # -------------------
    # LSTMs expect a 3-dim input of the form [samples, timesteps, features]
    if use_cal_vars:
        input_layer = Input(shape=(nb_input_neurons, 5))
    else:
        input_layer = Input(shape=(nb_input_neurons, 1))
    # input_layer = Input(shape=(1, self.nb_input_neurons)) # TODO Dimension???!!

    # Number of hidden layers
    nb_layers = np.array(hidden_neurons).shape[0]
    if nb_layers > 1:
        x = LSTM(hidden_neurons[0], return_sequences=True)(input_layer)
        x = Dropout(dropout)(x) # dropout layer to prevent overfitting
    else:
        x = LSTM(hidden_neurons[0])(input_layer)
        x = Dropout(dropout)(x)
    iter_temp = 1
    for hn in hidden_neurons[1:]:
        if iter_temp == len(hidden_neurons) - 1:
            x = LSTM(hn)(x)
        else:
            # if some hidden layers have to be added return sequence
            x = LSTM(hn, return_sequences=True)(x)
        iter_temp = iter_temp + 1
        x = Dropout(dropout)(x)

    # Output layer is a pdf function with all power "bins", see theory
    pdf = Dense(len(pdf_sample_points), activation='softmax')(x)  # previous layers (x) are stacked
    model = Functional_model(input=input_layer, output=pdf) # LSTM model definition
    return model

def generate_model_name():
    """
    Generate the model's file name with its main characteristics
    """
    name = prefix  + \
           '_granu' + str(granularity_s) + \
           '_hor' + str(forecast_horizon_mins) + \
           '_lb' + str(look_back_mins) + \
           '_drop' + str(dropout) + \
           '_pdflen' + str(len(pdf_sample_points)) + \
           '_' + activation
    if use_cal_vars:
        name += '_cal'

    name += '_lay'
    for hn in hidden_neurons:
        name = name + str(hn) + '-'
    name = name[:-1]
    return name[:249]  # limit length of name for ntfs file system

def init_weights(model, model_name, model_directory, working_directory):
    """
	Tries to restore previously saved model weights.
	"""
    try:
        model.load_weights(os.path.join(model_directory, model_name + '.h5'))
        print(dt.datetime.now().strftime('%x %X') + ' Model ' + model_name + ' successfully restored.')
    except Exception:
        print(dt.datetime.now().strftime('%x %X') + ' No saved model found for ' + model_name + ' in ' + working_directory + '. Initializing new model.')

def write_training_log(history, model_name, granularity_s, 
                       forecast_horizon_mins, look_back_mins, 
                       hidden_neurons, forecast_type):
    """
    Write file with training's results
    """
    with open(os.path.join(working_directory, 'training_log.csv'), 'a') as log_file:
        log_file.write(model_name + ',' + \
                       str(granularity_s) + ',' + \
                       str(forecast_horizon_mins) + ',' + \
                       str(look_back_mins) + ',' + \
                       str(hidden_neurons) + ',' + \
                       str(forecast_type) + ',' + \
                       str(history.history['loss'][-1]) + ',' + \
                       str(history.history['val_loss'][-1]) + '\n')

def train(model, X_train, y_train,X_val, y_val, 
          batch_size, nb_epoch, verbose, patience, 
          model_directory, model_name, granularity_s, 
          forecast_horizon_mins, look_back_mins, hidden_neurons, 
          forecast_type, validation_split=0.0):
    """
    Train the model where X are model inputs (nb_examples, nb_inputs) and y are outputs (nb_examples,1)
    """
    csv_logger = CSVLogger(os.path.join(model_directory, 'log.csv'), append = True)
    #将epoch的训练结果保存在csv文件中，支持所有可被转换为string的值，包括1D的可迭代数值如np.ndarray.   append：默认为False，为True时csv文件如果存在则继续写入，为False时总是覆盖csv文件
    early_stopping = EarlyStopping(monitor='val_loss', mode='min', min_delta=0.01, patience=patience,
                                           verbose=verbose)
    
    if X_val is not None:
        history = model.fit(X_train, y_train, nb_epoch=nb_epoch, batch_size=batch_size,
                                         validation_split=validation_split, validation_data=(X_val, y_val),
                                         callbacks=[csv_logger, early_stopping], verbose=verbose)
    else:
        history = model.fit(X_train, y_train, epochs=nb_epoch, batch_size=batch_size,
                                         validation_split=validation_split,
                                         verbose=verbose)
    write_training_log(history, model_name, 
                       granularity_s, 
                       forecast_horizon_mins, 
                       look_back_mins, 
                       hidden_neurons, 
                       forecast_type)
    print('Model has been trained successfully.')
    model.save_weights(os.path.join(model_directory, model_name + '.h5'))

def fit_lstm(raw_data, forecast_horizon_mins,
                 look_back_mins,
                 hidden_neurons,
                 dropout,
                 epochs,
                 use_cal_vars,
                 pdf_sample_points_min,
                 pdf_sample_points_max,
                 pdf_resolution,
                 granularity_min='default'):

    # Safe model type
    model_type = 'lstm'

    # Empty previous model list (if existing)
    model_list = []

    use_cal_vars = use_cal_vars

    # Derive granularity in minutes from data or resample if specified
    if granularity_min == 'default':
        granularity = (raw_data.index[1] - raw_data.index[0]).seconds / 60
        custom_granularity = None
        #???
    else:
        custom_granularity = True
        granularity = granularity_min
        raw_data = raw_data.resample(str(granularity) + 'Min').interpolate(method="linear")

    # Determine number of lagged values:
    nb_lagged_vals = look_back_mins / granularity

    # Forecast horizon in granularity steps
    forecast_steps = forecast_horizon_mins / granularity

    # Fit a model for each forecasting step up to the forecasting horizon
    for i in range(int(forecast_steps)):
            print("Fit model " + str(i + 1) + "/" + str(forecast_steps))
            for_hor_iter = (i + 1) * granularity
            forecast_horizon_mins = for_hor_iter
            #???
            '''
            model = Prob_LSTM(model_identifier='some-arbitrary-id',
                                        forecast_type='watts',
										granularity_s=granularity * 60,
										forecast_horizon_mins=for_hor_iter,
										look_back_mins=look_back_mins,
										hidden_neurons=hidden_neurons,
										use_cal_vars=use_cal_vars,
										dropout=dropout,
										pdf_sample_points_min=pdf_sample_points_min,
										pdf_sample_points_max=pdf_sample_points_max,
										pdf_resolution=pdf_resolution)
            '''
            model = generate_model()
            model_name = generate_model_name()
            model_directory = os.path.join(working_directory, trained_models_folder, model_name + '/')
            
            
            
            
            '''
            for new model active
            '''
            #os.mkdir(model_directory)
            ###mind this
            
            
            
            
            
            (X_train, y_train, ground_truth_train, t0_train), \
            (X_val, y_val, ground_truth_val, t0_val), \
            (X_test, y_test, ground_truth_test, t0_test), \
            scaling_factor = generate_training_data_lstm(
                   raw_data,
                   train_cv_test_split=train_cv_test_split, 
                   cleanse=False)
            			
            init_weights(model, model_name, model_directory, working_directory)
            
            
            optimizer = SGD(lr=learning_rate, momentum=0.0, decay=0.0, nesterov=False)
            model.compile(loss=loss_func, optimizer='adam')
            
            train(model, X_train, y_train, X_val, y_val, batch_size, nb_epoch, verbose, patience, model_directory, model_name, granularity_s, forecast_horizon_mins, look_back_mins, hidden_neurons, forecast_type)
            
            model_list.append(model)  
            
    return model_list, custom_granularity, granularity, nb_lagged_vals, use_cal_vars, model_type, scaling_factor, (X_test, y_test, ground_truth_test, t0_test)

def predict_on_preprocessed_input_lstm(X, model):
        """
        Employ model to predict X values
        """
        if X.shape[1] != nb_input_neurons:
            print(dt.datetime.now().strftime('%x %X') + ' Dim 1 of X does not match number of input neuros.')
            return

        y_pred = model.predict(X) / pdf_granularity

        batch_size = y_pred.shape[0]
        nb_sample_points = len(pdf_sample_points)
        pdf_sp_grid = np.repeat(pdf_sample_points.reshape((1, nb_sample_points)), batch_size, axis=0)
        return (pdf_sp_grid, y_pred)

def predict(recent_data, model_list, 
            custom_granularity, 
            granularity, 
            nb_lagged_vals, 
            use_cal_vars, 
            model_type, 
            scaling_factor):

    # Check if fitted model is available
    if not model_list:
        raise NotFittedError("NotFittedError: The model instance is not fitted yet. Call 'fit' with appropriate "
                             "arguments before using this method." % {'name': 'SVR'})

    # Resample if required
    if custom_granularity:
        recent_data = recent_data.resample(str(granularity) + 'Min').interpolate(method="linear")

    # Extract features
    lagged_vals = recent_data.watthour.values[-int(nb_lagged_vals):]
    t = recent_data.index[-1]
    if use_cal_vars:
        cal_vars = np.array([t.minute / 60.0, t.hour / 24.0, t.dayofweek / 7.0, t.month / 12.0])
    expected_vals = []
    timestamps = []

    if model_type == 'mdn':

        # Estimate for each forecast step
        for model in model_list:
            prprc_in = scale(lagged_vals, scaling_factor)
            if use_cal_vars:
                prprc_in = np.hstack((prprc_in, cal_vars))
            (coeffs, std_devs, centroids) = model.predict_on_preprocessed_input(prprc_in.reshape(1, len(prprc_in)))
            estimate = np.sum(np.multiply(coeffs, centroids), axis=1)
            expected_vals.append(estimate[0])
            t = t + dt.timedelta(minutes=granularity)
            timestamps.append(t)

    if model_type == 'sdn':

        # Estimate for each forecast step
        for model in model_list:
            prprc_in = scale(lagged_vals, scaling_factor)
            if use_cal_vars:
                prprc_in = np.hstack((prprc_in, cal_vars))
            (pdf_sp, pdf_sv) = model.predict_on_preprocessed_input(prprc_in.reshape(1, len(prprc_in)))
            intervals = pdf_sp[:, 1:] - pdf_sp[:, :-1]
            sp = 0.5 * (pdf_sp[:, 1:] + pdf_sp[:, :-1])
            sv = 0.5 * (pdf_sv[:, 1:] + pdf_sv[:, :-1])
            estimate = np.sum(np.multiply(sp, np.multiply(sv, intervals)), axis=1)
            expected_vals.append(estimate[0])
            t = t + dt.timedelta(minutes=granularity)
            timestamps.append(t)


    if model_type == 'lstm':

        # Estimate for each forecast step
        for model in model_list:
            prprc_in = scale(lagged_vals, scaling_factor)
            prprc_in = prprc_in.reshape((1, lagged_vals.size, 1))

            if use_cal_vars:
                cal_vars_reshaped = np.zeros((1, lagged_vals.size, 4))
                minutes = np.zeros(prprc_in.shape)
                hours = np.zeros(prprc_in.shape)
                days = np.zeros(prprc_in.shape)
                months = np.zeros(prprc_in.shape)
                for i in range(lagged_vals.size):
                    minutes[0][i][0] = cal_vars[0]
                    hours[0][i][0] = cal_vars[1]
                    days[0][i][0] = cal_vars[2]
                    months[0][i][0] = cal_vars[3]
                prprc_in = np.concatenate((prprc_in, minutes, hours, days, months), axis=2)

            # print(prprc_in)
            (pdf_sp, pdf_sv) = predict_on_preprocessed_input_lstm(prprc_in, model)
            intervals = pdf_sp[:, 1:] - pdf_sp[:, :-1]
            sp = 0.5 * (pdf_sp[:, 1:] + pdf_sp[:, :-1])
            sv = 0.5 * (pdf_sv[:, 1:] + pdf_sv[:, :-1])
            estimate = np.sum(np.multiply(sp, np.multiply(sv, intervals)), axis=1)
            expected_vals.append(estimate[0])
            t = t + dt.timedelta(minutes=granularity)
            timestamps.append(t)



    prediction = pd.DataFrame({'watthour': expected_vals}, timestamps)

    return prediction

def predict_on_test_set(X_test, model_list, 
            custom_granularity, 
            granularity, 
            nb_lagged_vals, 
            use_cal_vars, 
            model_type, 
            scaling_factor):

    # Check if fitted model is available
    if not model_list:
        raise NotFittedError("NotFittedError: The model instance is not fitted yet. Call 'fit' with appropriate "
                             "arguments before using this method." % {'name': 'SVR'})

    # Resample if required
    if custom_granularity:
        recent_data = recent_data.resample(str(granularity) + 'Min').interpolate(method="linear")

    expected_vals = []
    timestamps = []

    if model_type == 'mdn':

        # Estimate for each forecast step
        for model in model_list:
            prprc_in = scale(lagged_vals, scaling_factor)
            if use_cal_vars:
                prprc_in = np.hstack((prprc_in, cal_vars))
            (coeffs, std_devs, centroids) = model.predict_on_preprocessed_input(prprc_in.reshape(1, len(prprc_in)))
            estimate = np.sum(np.multiply(coeffs, centroids), axis=1)
            expected_vals.append(estimate[0])
            t = t + dt.timedelta(minutes=granularity)
            timestamps.append(t)

    if model_type == 'sdn':

        # Estimate for each forecast step
        for model in model_list:
            prprc_in = scale(lagged_vals, scaling_factor)
            if use_cal_vars:
                prprc_in = np.hstack((prprc_in, cal_vars))
            (pdf_sp, pdf_sv) = model.predict_on_preprocessed_input(prprc_in.reshape(1, len(prprc_in)))
            intervals = pdf_sp[:, 1:] - pdf_sp[:, :-1]
            sp = (pdf_sp[:, 1:] + pdf_sp[:, :-1])
            sv = (pdf_sv[:, 1:] + pdf_sv[:, :-1])
            estimate = np.sum(np.multiply(sp, np.multiply(sv, intervals)), axis=1)
            expected_vals.append(estimate[0])
            t = t + dt.timedelta(minutes=granularity)
            timestamps.append(t)


    if model_type == 'lstm':

        # Estimate for each forecast step
        for model in model_list:
            prprc_in = X_test
            # print(prprc_in)
            (pdf_sp, pdf_sv) = predict_on_preprocessed_input_lstm(prprc_in, model)
            intervals = pdf_sp[:, 1:] - pdf_sp[:, :-1]
            sp = 0.5 * (pdf_sp[:, 1:] + pdf_sp[:, :-1])
            sv = 0.5 * (pdf_sv[:, 1:] + pdf_sv[:, :-1])
            estimate = np.sum(np.multiply(sp, np.multiply(sv, intervals)), axis=1)
            expected_vals = estimate

    #prediction = pd.DataFrame({'watthour': expected_vals}, timestamps)

    return expected_vals, pdf_sv
'''
define all functions end
'''

"""
Runs training using a preprocessed dataset file.
Dataset_identifier e.g. 'ukdale_1', the prefix from config.py without the granularity_s suffix
"""

'''
generate dataset filename, dataset preprocess, open datasetfile
'''

filename = generate_dataset_filename(dataset_identifier=dataset_identifier)

dataset = open_dataset_file(filename, preprocessed_datasets_folder)



model_list, custom_granularity, granularity, nb_lagged_vals, \
use_cal_vars, model_type, scaling_factor, \
(X_test, y_test, ground_truth_test, t0_test) = fit_lstm(dataset, forecast_horizon_mins,
                 look_back_mins,
                 hidden_neurons,
                 dropout,
                 epochs,
                 use_cal_vars,
                 pdf_sample_points_min,
                 pdf_sample_points_max,
                 pdf_resolution)

#dataset_for_test = utils.open_dataset_for_test_file('for_test.csv', working_directory, preprocessed_datasets_folder)

#prediction = predict(X_test, model_list, 
                     #custom_granularity, 
                     #granularity, 
                     #nb_lagged_vals, 
                     #use_cal_vars, 
                     #model_type, 
                     #scaling_factor)
                     
prediction_testset, pdf_sv = predict_on_test_set(X_test, model_list, 
                     custom_granularity, 
                     granularity, 
                     nb_lagged_vals, 
                     use_cal_vars, 
                     model_type, 
                     scaling_factor)