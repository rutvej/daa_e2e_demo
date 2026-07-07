from flask import Flask, jsonify, request
import logging
import os

logging.basicConfig(level=logging.INFO)

from daa_sdk import DaaSdk
app = Flask(__name__)
daa_sdk = DaaSdk(backend_url=os.environ.get('DAA_BACKEND_API_URL'))

@app.route("/")
def hello_world():
    return "Hello, World!"

class MyObject:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hello, {self.name}!"

@app.route("/attribute-error")
def attribute_error():
    try:
        my_obj = MyObject("world")
        return my_obj.farewell()
    except Exception as e:
        logging.error("AttributeError occurred", exc_info=True)
        daa_sdk.capture_exception(e)
        return jsonify({"error": "Internal Server Error"}), 500

@app.route("/import-error")
def import_error():
    try:
        import non_existent_module
    except Exception as e:
        logging.error("ImportError occurred", exc_info=True)
        daa_sdk.capture_exception(e)
        return jsonify({"error": "Internal Server Error"}), 500

@app.route("/index-error")
def index_error():
    try:
        my_list = [1, 2, 3]
        return str(my_list[3])
    except Exception as e:
        logging.error("IndexError occurred", exc_info=True)
        daa_sdk.capture_exception(e)
        return jsonify({"error": "Internal Server Error"}), 500

@app.route("/name-error")
def name_error():
    try:
        return non_existent_variable
    except Exception as e:
        logging.error("NameError occurred", exc_info=True)
        daa_sdk.capture_exception(e)
        return jsonify({"error": "Internal Server Error"}), 500

@app.route("/recursion-error")
def recursion_error():
    try:
        def recursive_function():
            return recursive_function()
        recursive_function()
    except Exception as e:
        logging.error("RecursionError occurred", exc_info=True)
        daa_sdk.capture_exception(e)
        return jsonify({"error": "Internal Server Error"}), 500

@app.route("/new-error", methods=['POST'])
def new_error():
    param = request.args.get('param')
    try:
        data = request.get_json()
        if not data:
            data = {}
    except Exception as e:
        logging.error("Failed to parse JSON body.", exc_info=True)
        data = {}

    logging.info(f"Received request with param: {param} and data: {data}")

    if not param:
        logging.error("'param' is a required query parameter.")
        return jsonify({"error": "'param' is a required query parameter."}), 400

    if 'number' not in data:
        logging.error("'number' is a required field in the JSON body.", extra={'request_data': data})
        return jsonify({"error": "'number' is a required field in the JSON body."}), 400

    try:
        result = int(param) / data['number']
        logging.info(f"Calculation result: {result}")
        return jsonify({"result": result})
    except ZeroDivisionError as e:
        logging.error("Division by zero error.", exc_info=True)
        return jsonify({"error": "Division by zero is not allowed."}), 400
    except (ValueError, TypeError) as e:
        logging.error(f"Invalid input provided: {e}", exc_info=True)
        return jsonify({"error": "Invalid input: 'param' and 'number' must be numbers."}), 400

@app.route("/key-error")
def key_error():
    try:
        my_dict = {"name": "test"}
        return my_dict["age"]
    except Exception as e:
        logging.error("KeyError occurred", exc_info=True)
        daa_sdk.capture_exception(e)
        return jsonify({"error": "Internal Server Error"}), 500

@app.route("/type-error")
def type_error():
    try:
        result = "hello" + 5
        return str(result)
    except Exception as e:
        logging.error("TypeError occurred", exc_info=True)
        daa_sdk.capture_exception(e)
        return jsonify({"error": "Internal Server Error"}), 500

@app.route("/value-error")
def value_error():
    try:
        int("hello")
    except Exception as e:
        logging.error("ValueError occurred", exc_info=True)
        daa_sdk.capture_exception(e)
        return jsonify({"error": "Internal Server Error"}), 500
