from flask import Flask, render_template, request, redirect, url_for, session
from flask_caching import Cache
from markupsafe import escape
from flaskext.mysql import MySQL
import pymysql
import requests
import re, yaml, io
import datetime
import json

app = Flask(__name__)

# cache config
cache_config = {
    "DEBUG" : True,
    "CACHE_TYPE" : "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT" : 100000
}
app.config.from_mapping(cache_config)
cache = Cache(app)

# tax rate based on membership type
tax_rate_gold_member = 5.0
tax_rate_silver_member = 7.0

# load the config values from yaml file
with open("config.yaml", "r") as stream:
    data_loaded = yaml.safe_load(stream)
config = data_loaded['DATABASE']

# initialize all the key value pairs required for the mysql connection
app.secret_key = 'password123'
app.config['MYSQL_DATABASE_USER'] = config['USERNAME']
app.config['MYSQL_DATABASE_PASSWORD'] = config['PASSWORD']
app.config['MYSQL_DATABASE_DB'] = config['DB']

mysql = MySQL(app)

# returns the dictionary from byte string
def get_json_data(req):
    bytes_response = request.data
    json_response = bytes_response.decode('utf8').replace("'", '"')
    obj = json.loads(json_response)
    return obj

# return the current date time as per the specification of the db
def get_current_datetime():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# will store the response of sql query in a 2d matrix and return
def beautify_sql_response_pending_transaction(data):
    res = []

    for row in range(len(data)):
        temp = []
        for col in range(len(data[row])):
            if isinstance(data[row][col], datetime.datetime):
                t = data[row][col]
                t = t.isoformat()
                temp.append(t)
            else:
                temp.append(data[row][col])
        res.append(temp)

    return res

# get the current rate of bitcoin
def get_current_rate():
    response = requests.get("https://api.coindesk.com/v1/bpi/currentprice.json")
    return response.json()['bpi']['USD']['rate_float']


# update transaction table based on the decision of the user
def update_transaction_table(client_decision):

    cursor = mysql.get_db().cursor()

    for val in client_decision:
        if val['transaction_type'] == 'BUY':

            cursor.execute('SELECT * FROM ACC_DETAILS WHERE ClientId = %s', (val['client_id'], ))
            acc_detail = cursor.fetchone()

            rate = get_current_rate()
            commission = val['commission_paid']
            amt_to_buy = val['bitcoin_amt']
            total_amt_to_be_paid = float(commission) + (float(amt_to_buy) * float(rate))
            if acc_detail[2] < total_amt_to_be_paid :
                return 'error'
            else :
                # update the transaction table
                cursor.execute('UPDATE TRANSACTION SET Status = %s WHERE TransactionId = %s',
                               (val['decision'], val['transaction_id'], ))

                if val['decision'] == 'reject':
                    mysql.get_db().commit()
                    return
                fiat_currency = float(acc_detail[2]) - float(total_amt_to_be_paid)
                total_amt = fiat_currency + (float(amt_to_buy) * float(rate))

                # update the acc_details table
                cursor.execute('UPDATE ACC_DETAILS SET FiatCurrency = %s WHERE ClientId = %s ',
                               ( fiat_currency, val['client_id'], ))

                # update the bitcoin table
                cursor.execute('SELECT * FROM BITCOIN WHERE ClientId = %s',(val['client_id'], ))
                client_bitcoin_detail = cursor.fetchone()
                cursor.execute('UPDATE BITCOIN SET Units = %s WHERE ClientId = %s',
                               (float(client_bitcoin_detail[1])+float(val['bitcoin_amt']), val['client_id']))
                mysql.get_db().commit()
        else:
            print("Sell")
    return "hello"

#--------------------Needs to completed--------------------------------------------
# fetch the data which need to be shown to respective user.
def get_pending_data(user_type, client_id=0):
    cursor = mysql.get_db().cursor()

    if user_type == 'silver' or user_type == 'gold':
        cursor.execute('SELECT * FROM TRANSACTION WHERE ClientId = %s AND Status = %s', (client_id, "pending"))
        data = cursor.fetchall()
        return beautify_sql_response_pending_transaction(data)
    elif user_type == 'admin':
        return 'admin'
    else :
        cursor.execute('SELECT * FROM TRANSACTION WHERE Status = %s ', ("pending", ))
        data = cursor.fetchall()
        return beautify_sql_response_pending_transaction(data)

# get details of bitcoin
def get_user_bitcoin_details(client_id):
    cursor = mysql.get_db().cursor()
    cursor.execute('SELECT Units FROM BITCOIN WHERE ClientId = %s', (client_id,))
    units = cursor.fetchone()
    if units is None:
        return None
    return units[0]

# get balance details
def get_account_details(client_id):
    cursor = mysql.get_db().cursor()
    cursor.execute('SELECT * FROM ACC_DETAILS WHERE ClientId = %s', (client_id, ))
    res = cursor.fetchone()
    if res is None:
        return None
    return list(res)

# get the users details or the status based on the flag return_status
def get_user_details(user_name, password, user_type, return_status):
    cursor = mysql.get_db().cursor()

    if not return_status:
        cursor.execute('SELECT * FROM Users WHERE UserName = %s AND Password = %s and Type IN %s ',
                       (user_name, password, user_type,))
        account = cursor.fetchone()
        if account is None:
            return None
        return list(account)
    else:
        cursor.execute('SELECT Type FROM Users WHERE UserName = %s', (user_name, ))
        status = cursor.fetchone()
        return status[0]

# make session time 5 min
@app.before_request
def make_session_permanent():
    app.permanent_session_lifetime = datetime.timedelta(minutes=5)

# homepage/login route
@app.route("/")
@app.route("/login", methods=['GET','POST'])
def login():
    msg = ''
    user_type = ''
    file_load=''
    data = ''
    acc_details = []
    account = []

    # check is user is already logged in
    if len(session) > 0 and session['loggedin']:
        acc_details = get_account_details(session['id'])
        membership_type = get_user_details(session['username'], '', '', True)
        data = get_pending_data(membership_type,session['id'])
        units = get_user_bitcoin_details(session['id'])
        return render_template(session['file_redirect'], msg=session['msg'], data=data, acc_details=acc_details,
                               membership_type=membership_type, bitcoin_unit=units, bitcoin_rate=get_current_rate())

    if request.method=='POST' and 'username' in request.form and 'password' in request.form and \
            ('checkuser' in request.form or 'checkadmin' in request.form or 'checktrader' in request.form):
        user_name = request.form['username']
        password = request.form['password']
        if 'checkuser' in request.form :
            user_type = ['silver','gold']
            file_load = 'index.html'
        elif 'checktrader' in request.form:
            user_type = ['trader']
            file_load = 'trader.html'
        else:
            user_type = ['admin']
            file_load = 'admin.html'

        #check if the user exists in db or no
        account = get_user_details(user_name, password, user_type, False)

        if account:
            # get the account details associated with the user
            acc_details = get_account_details(account[0])
            msg = 'Logged in successfully !'

            # get user bitcoin units of a user
            units = get_user_bitcoin_details(account[0])

            # get data based on the user type and render that specific template
            data = get_pending_data(account[7], account[0])

            #store in session
            session['loggedin'] = True
            session['id'] = account[0]
            session['username'] = account[1]
            session['file_redirect'] = file_load
            session['msg'] = msg
            return render_template(file_load, msg=msg, data=data, acc_details=acc_details,
                                   membership_type=account[7], bitcoin_unit=units, bitcoin_rate=get_current_rate())
        else:
            msg = 'Incorrect username / password !'

    return render_template('login.html', msg=msg)

# logout route
@app.route('/logout')
def logout():
    session.pop('loggedin', None)
    session.pop('id', None)
    session.pop('username', None)
    session.pop('file_redirect', None)
    session.pop('msg', None)
    return redirect(url_for('login'))

# sign up route
@app.route('/register', methods=['GET', 'POST'])
def register():
    msg = ''

    if request.method == 'POST' and 'username' in request.form and 'firstname' in request.form and 'lastname' in request.form and \
            'password' in request.form and 'email' in request.form and 'phone' in request.form and 'phone' in request.form and \
            'staddress' in request.form and 'city' in request.form and 'zip' in request.form and 'state' in request.form:
        username = request.form['username']
        first_name = request.form['firstname']
        last_name = request.form['lastname']
        password = request.form['password']
        email = request.form['email']
        phone = request.form['phone']
        zip = request.form['zip']
        state = request.form['state']
        city = request.form['city']
        street_address = request.form['staddress']
        cursor = mysql.get_db().cursor()
        cursor.execute('SELECT * FROM USERS WHERE UserName = %s ', (username,))
        account = cursor.fetchone()
        if account:
            msg = 'Account already exists !'
        elif not re.match(r'[^@]+@[^@]+\.[^@]+', email):
            msg = 'Invalid email address !'
        elif not re.match(r'[A-Za-z0-9]+', username):
            msg = 'Username must contain only characters and numbers !'
        elif not username or not password or not email or not phone:
            msg = 'Please fill out the form !'
        else:
            cursor.execute('INSERT INTO USERS VALUES (NULL, % s, %s, %s, % s, % s, %s, %s)', (username, first_name, last_name,
                                                                                      password, phone, email, "silver",))
            cursor.execute('SELECT ClientId FROM USERS WHERE UserName = %s ', (username,))
            client_id = cursor.fetchone()[0]
            cursor.execute('INSERT INTO ADDRESS VALUES (%s, %s, %s, %s, %s)', (client_id, street_address, city, state, zip))

            # inserting dummy data in acc and bitcoin table
            bitcoin_rate = get_current_rate()
            total_amt = 100000 + (2 * bitcoin_rate)
            cursor.execute('INSERT INTO ACC_DETAILS VALUES (%s, %s)', (client_id, '100000'))
            cursor.execute('INSERT INTO BITCOIN VALUES (%s, %s)',( client_id, 2))
            mysql.get_db().commit()
            msg = 'You have successfully registered !'
    elif request.method == 'POST':
        msg = 'Please fill out the form !'

    return render_template('register.html', msg=msg)

# fetch all the data for a specific user from Transaction table based on the client_id
@app.route('/userdata/<client_id>')
def userdata(client_id):
    msg=''

    #check if session is lost
    if len(session) == 0 :
        return render_template('login.html')

    # fetch the users past transactions
    cursor = mysql.get_db().cursor()
    cursor.execute('SELECT * FROM Transaction WHERE ClientId = %s AND DATE < NOW() AND Status != %s ', (client_id,"pending", ))
    data = cursor.fetchall()

    # if the length of data fetched from sql is zero, this means that the user is trading for the first time
    if len(data)==0:
        msg='This is the first transaction'

    return render_template('userdata.html', msg=msg, data=data)

# this will insert the details in seller table regarding the details of the seller
@app.route('/sell_bitcoin', methods=['POST'])
def sell_bitcoin():
    obj = get_json_data(request.data)
    client_id = obj["ClientId"]
    transaction_id = obj["TransactionId"]
    transaction_type = obj["TransactionType"]
    membership_type = obj["MembershipType"]
    bitcoin_unit_to_sold = obj["BitcoinSell"]

    commission_type = 0
    if membership_type == 'gold':
        commission_type = tax_rate_gold_member
    else:
        commission_type = tax_rate_silver_member

    rate = get_current_rate()
    commission_paid = float(bitcoin_unit_to_sold) * float(rate)/100

    cursor = mysql.get_db().cursor()
    cursor.execute('INSERT INTO SELLER VALUES (%s, %s, %s, %s, %s)',
                   (client_id, bitcoin_unit_to_sold, get_current_datetime(), commission_paid, commission_type))
    mysql.get_db().commit()
    return json.dumps({'success':True})

# create an entry in the transaction table for the current transaction
@app.route('/buy_bitcoin', methods=['POST'])
def buy_bitcoin():
    obj = get_json_data(request.data)
    client_id = obj["ClientId"]
    recipient_id = obj["RecipientId"]
    transaction_id = obj["TransactionId"]
    membership_type = obj["MembershipType"]
    bitcoin_unit_to_buy = obj["BitcoinBuy"]

    commission_type = 0
    if membership_type == 'gold':
        commission_type = tax_rate_gold_member
    else:
        commission_type = tax_rate_silver_member

    rate = get_current_rate()
    commission_paid = float(bitcoin_unit_to_buy) * float(rate)/100

    cursor = mysql.get_db().cursor()
    cursor.execute('INSERT INTO TRANSACTION VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                   (client_id, transaction_id, "BUY", get_current_datetime(), commission_type, commission_type, recipient_id, bitcoin_unit_to_buy, 'pending',))
    mysql.get_db().commit()

    return json.dumps({'success':True})

# update a list of transactions which is selected by the trader
@app.route('/update_transaction', methods=['POST'])
def update_transaction():
    client_decision = []

    # store the client_id, transaction_id and decision in a list of dictionary
    for client_transaction in request.form:
        temp = client_transaction.split("+")
        """
        clientid=temp[0], transactionid=temp[1], transactiontype=temp[2], commissionpaid=temp[3], commissiontype=temp[4], 
        recipientid=temp[5], bitcoinamt=temp[6]
        """
        t = dict()
        t['client_id'] = temp[0]
        t['transaction_id'] = temp[1]
        t['transaction_type'] = temp[2]
        t['commission_paid'] = temp[3]
        t['commission_type'] = temp[4]
        t['recipient_id'] = temp[5]
        t['decision'] = "completed" if request.form[client_transaction]=="accept" else "reject"
        t['bitcoin_amt'] = temp[6]
        client_decision.append(t)
    update_transaction_table(client_decision)

    return redirect(url_for('login'))