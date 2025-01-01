import pytest
from unittest.mock import patch

from rotkehlchen.accounting.structures.balance import Balance
from rotkehlchen.history.events.structures.asset_movement import AssetMovement
from rotkehlchen.history.events.structures.types import HistoryEventType
from rotkehlchen.exchanges.swyftx import Swyftx
from rotkehlchen.types import Timestamp, Location
from rotkehlchen.errors.misc import RemoteError

def test_swyftx_query_balances(mock_swyftx):
    def mock_balances_request(method, url, headers, params):
        return {
            'balances': [
                {
                    'symbol': 'BTC',
                    'total': '0.5',
                    'usdValue': 25000.0
                },
                {
                    'symbol': 'ETH',
                    'total': '1.2',
                    'usdValue': 3600.0
                }
            ]
        }

    with patch.object(mock_swyftx.session, 'get', side_effect=mock_balances_request):
        balances, msg = mock_swyftx.query_balances()

    assert msg == ''
    assert balances is not None
    assert len(balances) == 2
    assert balances['BTC'].amount == 0.5
    assert balances['BTC'].usd_value == 12500.0
    assert balances['ETH'].amount == 1.2
    assert balances['ETH'].usd_value == 3600.0

def test_swyftx_query_online_history_events(mock_swyftx):
    def mock_deposit_withdrawal_request(method, url, headers, params):
        if 'deposit' in url:
            return {
                'items': [
                    {
                        'assetCode': 'BTC',
                        'amount': '0.5',
                        'timestamp': 1670000000,
                        'id': 'dep1'
                    }
                ]
            }
        elif 'withdraw' in url:
            return {
                'items': [
                    {
                        'assetCode': 'ETH',
                        'amount': '1.2',
                        'timestamp': 1671000000,
                        'id': 'with1'
                    }
                ]
            }

    with patch.object(mock_swyftx.session, 'get', side_effect=mock_deposit_withdrawal_request):
        events = mock_swyftx.query_online_history_events(
            start_ts=Timestamp(1669000000),
            end_ts=Timestamp(1672000000),
        )

    assert len(events) == 2
    deposit_event = events[0]
    assert deposit_event.event_type == HistoryEventType.DEPOSIT
    assert deposit_event.asset.identifier == 'BTC'
    assert deposit_event.balance.amount == 0.5
    assert deposit_event.timestamp == 1670000000

    withdrawal_event = events[1]
    assert withdrawal_event.event_type == HistoryEventType.WITHDRAWAL
    assert withdrawal_event.asset.identifier == 'ETH'
    assert withdrawal_event.balance.amount == 1.2
    assert withdrawal_event.timestamp == 1671000000

def test_swyftx_query_trade_history(mock_swyftx):
    def mock_trade_request(method, url, headers, params):
        return {
            'trades': [
                {
                    'baseAsset': 'BTC',
                    'quoteAsset': 'USD',
                    'side': 'buy',
                    'amount': '0.1',
                    'price': '20000',
                    'fee': '10',
                    'feeAsset': 'USD',
                    'timestamp': 1670000000
                }
            ]
        }

    with patch.object(mock_swyftx.session, 'get', side_effect=mock_trade_request):
        trades = mock_swyftx.query_trade_history(
            start_ts=Timestamp(1669000000),
            end_ts=Timestamp(1672000000),
        )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.base_asset.identifier == 'BTC'
    assert trade.quote_asset.identifier == 'USD'
    assert trade.trade_type == 'buy'
    assert trade.amount == 0.1
    assert trade.rate == 20000
    assert trade.fee == 10
