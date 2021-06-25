#
# Copyright 2020, Fernando Lemes da Silva
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import os
import sys
import logging
import threading
from pymongo import MongoClient
import json
import pika
import time
import random
import uuid
from PathAggregator import PathAggregator
from flask import Flask

logger = logging.getLogger()
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

#
# Retrieve RabbitMQ settings
#
if 'RABBITMQ_HOST' in os.environ:
	rabbitmq_host = os.environ['RABBITMQ_HOST']
	logger.info('Using RabbitMQ host: %s', rabbitmq_host)
else:
	logger.fatal('Missing RABBITMQ_HOST environment variable.')
	sys.exit()

if 'RABBITMQ_QUEUE' in os.environ:
	rabbitmq_queue = os.environ['RABBITMQ_QUEUE']
	logger.info('RabbitMQ queue: %s', rabbitmq_queue)
else:
	logger.fatal('Missing RABBITMQ_QUEUE environment variable.')
	sys.exit()

if 'RABBITMQ_EXCHANGE' in os.environ:
	rabbitmq_exchange = os.environ['RABBITMQ_EXCHANGE']
else:
	rabbitmq_exchange = "enriched_records"
logger.info('RabbitMQ exchange: %s', rabbitmq_exchange)

if 'MONGO_URL' in os.environ:
	mongo_url = os.environ['MONGO_URL']
	logger.info('Using mongo URL: %s', mongo_url)
else:
	logger.fatal('Missing MONGO_URL environment variable.')
	sys.exit()

if 'MONGO_DATABASE' in os.environ:
	mongo_database = os.environ['MONGO_DATABASE']
	logger.info('Using mongo database: %s', mongo_database)
else:
	logger.fatal('Missing MONGO_DATABASE environment variable.')
	sys.exit()

if 'MONGO_COLLECTION' in os.environ:
	mongo_collection = os.environ['MONGO_COLLECTION']
	logger.info('Using mongo collection: %s', mongo_collection)
else:
	logger.fatal('Missing MONGO_COLLECTION environment variable.')
	sys.exit()


flask_app = Flask(__name__)

rabbit_ok = True
mongo_ok = True

@flask_app.route('/healthcheck')
def healthcheck():
	if rabbit_ok and mongo_ok:
		return 'OK', 200
	else:
		response = ''
		if not rabbit_ok:
			response += 'RabbitMQ NOK '
		if not mongo_ok:
			response += 'MongoDB NOK'
		return response, 400

path_aggregator = PathAggregator()

def publish_message(channel, data):
	global rabbit_ok
	message = json.dumps(data)
	logger.debug('Sending processed document to RabbitMQ: %s', message)
	try:
		channel.basic_publish(exchange=rabbitmq_exchange,
							  routing_key='',
							  body=message,
							  properties=pika.BasicProperties(delivery_mode = 2))
		rabbit_ok = True
	except:
		logger.error('Error sending data to RabbitMQ.')
		rabbit_ok = False


def insert_into_database(collection, data):
	global mongo_ok
	logger.debug('Sending processed document to MongoDB: %s', json.dumps(data))
	try:
		collection.insert_one(data)
		mongo_ok = True
	except:
		logger.error('Error sending data to MongoDB.')
		mongo_ok = False


def enrich_data(data):
	data['aggregated_http_path'] = path_aggregator.get_path_aggregator(data['http_verb'] + data['http_path'])
	data['uuid'] = str(uuid.uuid4())
	data['random'] = random.randint(0, 65535)
	return data


def run_queue_listener():
	global rabbit_ok
	while True:

		connected = False
		while not connected:
			try:
				connection = pika.BlockingConnection(pika.ConnectionParameters(rabbitmq_host))
				channel = connection.channel()
				channel.queue_declare(queue=rabbitmq_queue)
				channel.exchange_declare(exchange=rabbitmq_exchange, exchange_type='fanout')
				connected = True
			except pika.exceptions.AMQPConnectionError:
				logger.info('Waiting before retrying RabbitMQ connection...')
				time.sleep(15)

		mongo_client = MongoClient(mongo_url)
		database = mongo_client[mongo_database]
		collection = database[mongo_collection]

		def callback(channel, method, properties, body):
			data = json.loads(body)
			data = enrich_data(data)
			insert_into_database(collection, dict(data))
			publish_message(channel, data)
		channel.basic_consume(queue=rabbitmq_queue, on_message_callback=callback, auto_ack=True)
		try:
			channel.start_consuming()
		except:
			rabbit_ok = False


if __name__ == "__main__":
	try:
		queue_listener_thread=threading.Thread(target=run_queue_listener)
		queue_listener_thread.start()
		flask_app.run(host='0.0.0.0', port=80)
	except (IOError, SystemExit):
		raise
	except KeyboardInterrupt:
		logger.info("Shutting down.")
		connection.close()
