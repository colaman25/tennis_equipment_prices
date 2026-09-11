from flask import Flask, json
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import json

df = pd.read_csv('data/direct_tennis_products.csv')
record_dict = json.loads(df.to_json(orient = "records"))

api = Flask(__name__)


@api.route('/products', methods=['GET'])
def get_products():
  return json.dumps(record_dict)

if __name__ == '__main__':
    api.run()