import os, time, random, asyncio
import pymongo
from pymongo import MongoClient
from datetime import datetime, timedelta
import uuid
from dotenv import load_dotenv
from bson.objectid import ObjectId
from fhict_cb_01.custom_telemetrix import CustomTelemetrix
from flask import Flask, render_template, request, redirect, url_for, session
import secrets

# Arduino variables

board = CustomTelemetrix()

red_pin = 4
green_pin = 5
yellow_pin = 7
buzzer = 3
cooking_state = False
LED_PINS = [red_pin, green_pin, yellow_pin]

# Arduino functions
def setup():
    for pin in LED_PINS:
        board.set_pin_mode_digital_output(pin)
    board.set_pin_mode_digital_output(buzzer)
    time.sleep(0.1)
    oven_empty()

async def wait(seconds):
    print("Start")
    await asyncio.sleep(seconds)  # Non-blocking sleep for 1 second
    print("End")

def turn_off_leds():
    for pin in LED_PINS:
        board.digital_write(pin, 0)
    time.sleep(0.1)

def oven_empty():
    turn_off_leds()
    board.digital_write(buzzer, 0)
    board.digital_write(red_pin, 1)
    board.displayShow("frEE")

def oven_cooking():
    turn_off_leds()
    cooking_time = random.randint(5, 6)
    board.digital_write(yellow_pin, 1)

    while cooking_time != 0:
        board.displayShow(cooking_time)
        asyncio.run(wait(1))
        cooking_time -= 1

    over_done()
    # some code to refresh the website after the time would be ideal, i tried some things but cant figure it put

def over_done():
    global cooking_state
    board.digital_write(yellow_pin, 0)
    board.digital_write(green_pin, 1)
    board.digital_write(buzzer, 1)
    board.displayShow("donE")
    cooking_state = False

setup()

# Generates a random URL-safe string of 24 characters


app = Flask(__name__)

load_dotenv()

# retrieve MongoDB link from environment variable
MONGO_DB_LINK = os.getenv("MONGO_DB_LINK")
password = str(os.getenv("PASSWORD"))

# create MongoClient instance
client = MongoClient(MONGO_DB_LINK)

# create or retrieve database
db = client["firstcome"]
orders = db['Orders']
carts = db['Carts']
sessions = db['Sessions']
products = db['Products']

app = Flask(__name__)
secret_key = secrets.token_urlsafe(24)
app.secret_key = secret_key


# Database Functions


## HOME ##

@app.route('/add_to_cart/<product_id>', methods=['POST'])
def add_to_cart(product_id):
    if 'uid' not in session:
        start_session()

    cart_data = {
        'session_id': str(session['uid']),
        'product_id': product_id,
        'timestamp': datetime.now()
    }

    carts.insert_one(cart_data)
    return redirect(url_for('home'))


@app.route('/')
def home():
    all_products = products.find()
    return render_template('home.html', products=all_products)


## CART CODE ##

def start_session():
    session['uid'] = uuid.uuid4()
    log_data = {
        'timestamp': datetime.now(),
        'session_id': str(session['uid']),
        'path': request.path,
        'method': request.method,
        'ip_address': request.remote_addr
    }
    sessions.insert_one(log_data)
    return log_data


def add_order(name, phone, address, postcode, delivery_method, order, status='Not Started'):
    # Get the current date and time
    current_date = datetime.now()

    # Prepare order data
    order_data = {
        'Date': current_date,
        'Name': name,
        'Phone': phone,
        'Address': address,
        'Postcode': postcode,
        'DeliveryMethod': delivery_method,
        'Order': order,
        'Status': status
    }

    orders.insert_one(order_data)


@app.route('/cart')
def cart():
    if 'uid' not in session:
        return redirect(url_for('home'))

    user_cart = carts.find({'session_id': str(session['uid'])})
    cart_products = []

    for item in user_cart:
        product = products.find_one({'_id': ObjectId(item['product_id'])})
        if product:
            cart_products.append(product)

    return render_template('cart.html', products=cart_products, session=str(session['uid']))


## DASHBOARD CODE ###

def get_order_data():
    return list(orders.find())


def update_status(order_id, new_status):
    global cooking_state
    order = orders.find_one({'_id': ObjectId(order_id)})
    current_status = order['Status']
    delivery_method = order['DeliveryMethod']

    status_sequence = ['Not Started', 'Preparation', 'Cooking',
                       'Take Out', 'Out for Delivery', 'Done']
    current_index = status_sequence.index(current_status)
    next_index = (current_index + 1) % len(status_sequence)
    if not cooking_state or current_index != 2:
        new_status = status_sequence[next_index]

    if current_index == 3:
        if delivery_method == 'Take Out':
            new_status = 'Take Out'
        elif delivery_method == 'Delivery':
            new_status = 'Out for Delivery'

    if current_index == 2 and delivery_method == 'Delivery' and not cooking_state:
        if current_status == 'Cooking':
            new_status = 'Out for Delivery'

    if current_index == 3 and delivery_method == 'Take Out':
        if current_status == 'Take Out':
            new_status = 'Done'

    if current_status == 'Cooking' and not cooking_state:
        cooking_state = True
        oven_cooking()
    elif not cooking_state:
        oven_empty()

    orders.update_one({'_id': ObjectId(order_id)}, {
                      '$set': {'Status': new_status}})


@app.route('/dashboard')
def dashboard():
    orders = get_order_data()
    # Format date and time in the orders
    for order in orders:
        # Assuming 'Date' is the field containing the date and time
        if 'Date' in order:
            # Assuming the date is stored as a string or a datetime object
            date_obj = order['Date']
            if isinstance(date_obj, str):
                # Convert the string to a datetime object
                # Adjust the format if needed
                date_obj = datetime.strptime(date_obj, '%Y-%m-%d %H:%M:%S')
            # Format date and time as desired
            order['Date'] = date_obj.strftime('%d %B %Y, %H:%M')
    return render_template('dashboard.html', orders=orders)


@app.route('/update_status', methods=['POST'])
def update_order_status():
    order_id = request.form['order_id']
    update_status(order_id, request.form['new_status'])
    return redirect('/dashboard')


@app.route('/delete_order', methods=['POST'])
def delete_order():
    order_id = request.form['order_id']
    orders.delete_one({'_id': ObjectId(order_id)})
    return redirect('/dashboard')


## TRACKER ##

@app.route('/tracker-login', methods=['GET', 'POST'])
def tracker_login():
    if request.method == 'POST':
        phone_number = request.form['phone_number']
        order = orders.find_one({'Phone': phone_number})
        if order:
            return redirect(url_for('tracker', phone_number=phone_number))
        else:
            return "Phone number not found. Please try again."

    return render_template('tracker_login.html')


@app.route('/tracker/<phone_number>')
def tracker(phone_number):
    order = orders.find_one({'Phone': phone_number})
    if order:
        return render_template('tracker.html', order=order, status=order['Status'])
    else:
        return "Order not found."

if __name__ == '__main__':
    app.run()

