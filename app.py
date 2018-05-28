import os
from decimal import Decimal, ROUND_DOWN

import dateutil.parser
from dateutil.tz import tzutc

import datetime
import gdax
import requests
from pprint import pformat as p

client = gdax.AuthenticatedClient(
    os.environ.get('GDAX_API_KEY'), os.environ.get('GDAX_API_SECRET'),
    os.environ.get('GDAX_PASSPHRASE'))


def get_bank_id(client=client, match_substring=''):
    account_type = 'ach_bank_account'
    payment_methods = client.get_payment_methods()
    for p in payment_methods:
        if p['type'] == account_type and match_substring in p['name']:
            print(p['name'])
            return p['id']


def get_completed_deposits(client=client):
    a = get_usd_account(client=client)
    history = client.get_account_history(a['id'])[0]
    for entry in history:
        if entry['type'] == 'transfer' and entry['details'][
                'transfer_type'] == 'deposit':
            yield entry


def get_all_deposits(client=client):
    a = get_usd_account(client=client)
    r = requests.get(client.url + '/accounts/{}/transfers'.format(a['id']),
                     auth=client.auth)
    return r.json()


def should_create_deposit(deposits, interval=datetime.timedelta(days=15)):
    for d in deposits:
        created_at = dateutil.parser.parse(d['created_at'])
        canceled_at = d['canceled_at']
        completed_at = dateutil.parser.parse(d['completed_at']) if d[
            'completed_at'] else None

        now = datetime.datetime.now(tzutc())
        if created_at > (now - interval):
            return False
    return True


def get_usd_account(client=client):
    accounts = client.get_accounts()
    for a in accounts:
        if a['currency'] == 'USD':
            return a


def allocation_amounts(amount, allocation_percentages):
    asset_allocation = dict()
    for currency, percentage in allocation_percentages.items():
        allocation = amount * Decimal(percentage)
        asset_allocation[currency] = allocation
    return asset_allocation


def get_available_to_trade(client=client):
    account = get_usd_account(client=client)
    return Decimal(account['available'])

def market_buy_params(product_id, amount_in_usd):
    funds = amount_in_usd.quantize(Decimal('.01'), rounding=ROUND_DOWN)
    return dict(
        product_id=product_id, type='market', side='buy', funds=str(funds))

def limit_buy_params(product_id, amount_in_usd, client=client):
    ticker = client.get_product_ticker(product_id)
    limit_price = Decimal(ticker['price']) - Decimal(0.50)
    amount_in_crypto = (amount_in_usd / limit_price).quantize(Decimal('.000000001'),
                                                              rounding=ROUND_DOWN)
    return dict(product_id=product_id,
                side='buy',
                price=str(limit_price),
                size=str(amount_in_crypto)
                )

def buy(currency, amount_in_usd, client=client,
        buy_param_fn=market_buy_params, dry_run=False):
    pair = '{}-USD'.format(currency)
    buy_params = buy_param_fn(pair, amount_in_usd)
    if dry_run:
        print('dry_run: {}'.format(currency))
        print(buy_params)
    else:
        return client.buy(**buy_params)


def allocate_usd(client=client,
                 get_available_to_trade=get_available_to_trade,
                 allocation_percentages=dict(),
                 minimum_available_to_trade=Decimal('100'),
                 no_fee=False):
    available = get_available_to_trade(client=client)
    buys = list()
    if available >= minimum_available_to_trade:
        amounts = allocation_amounts(available, allocation_percentages)
        for currency, amount_in_usd in amounts.items():
            if no_fee:
                buys.append(
                    buy(currency,
                        amount_in_usd,
                        client=client,
                        buy_param_fn=limit_buy_params)
                )
            else:
                buys.append(
                    buy(currency, amount_in_usd, client=client))
    return buys


def deposit(amount, account_name='', client=client, dry_run=False):
    payment_id = get_bank_id(client=client, match_substring=account_name)
    if dry_run:
        print('Dry run buy: {}'.format(amount))
    else:
        return client.deposit(
            amount=amount, currency='USD', payment_method_id=payment_id)


def main(deposit_account, deposit_amount, deposit_interval, min_available_to_trade,
         asset_allocation, no_fee, print_fn):
    prev_deposits = get_all_deposits()
    print_fn('Checking whether to deposit...')
    if should_create_deposit(prev_deposits, interval=deposit_interval):
        print_fn('No deposit in {}. Creating deposit for ${}.'.format(
            deposit_interval, deposit_amount))
        print_fn(p(deposit(deposit_amount, account_name=deposit_account)))
    else:
        print_fn('Skipped deposit.')

    print_fn('Checking whether to allocate...')
    allocations = allocate_usd(no_fee=no_fee,
        minimum_available_to_trade=min_available_to_trade,
        allocation_percentages=asset_allocation)
    if not allocations:
        print_fn('Skipped allocations.')
    else:
        print_fn(p(allocations))
    print_fn('Done.')
