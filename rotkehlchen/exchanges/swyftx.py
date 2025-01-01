import logging
import hmac
import hashlib
import time
import requests
from typing import Any, Dict, List, Tuple

from rotkehlchen.exchanges.exchange import ExchangeInterface, ExchangeQueryBalances
from rotkehlchen.exchanges.data_structures import Trade
from rotkehlchen.history.events.structures.asset_movement import AssetMovement
from rotkehlchen.history.events.structures.types import HistoryEventType
from rotkehlchen.accounting.structures.balance import Balance
from rotkehlchen.types import (
    ApiKey,
    ApiSecret,
    Fee,
    Location,
    Price,
    Timestamp,
    TradeType,
    AssetAmount,
)
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.errors.asset import UnknownAsset
from rotkehlchen.serialization.deserialize import deserialize_asset_amount

logger = logging.getLogger(__name__)

class Swyftx(ExchangeInterface):
    """
    Swyftx integration with Rotki.
    """

    BASE_URL = 'https://api.swyftx.com.au/'

    def __init__(
        self,
        name: str,
        api_key: ApiKey,
        secret: ApiSecret,
        database: 'DBHandler',
        msg_aggregator: 'MessagesAggregator',
    ) -> None:
        super().__init__(
            name=name,
            location=Location.create('SWYFTX'),
            api_key=api_key,
            secret=secret,
            database=database,
            msg_aggregator=msg_aggregator,
        )
        self.session.headers.update({'Content-Type': 'application/json'})

    def _generate_signature(self, path: str, timestamp: int) -> str:
        message = f'{path}{timestamp}'.encode()
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()

    def _make_request(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        timestamp = int(time.time() * 1000)
        signature = self._generate_signature(endpoint, timestamp)
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Swyftx-Signature': signature,
            'Swyftx-Timestamp': str(timestamp),
        }
        url = self.BASE_URL + endpoint

        try:
            response = self.session.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise RemoteError(f'Swyftx API request failed: {e}') from e

    def query_balances(self, **kwargs: Any) -> ExchangeQueryBalances:
        endpoint = 'user/balance'
        data = self._make_request(endpoint)

        assets_balance = {}
        for balance_entry in data.get('balances', []):
            try:
                asset = self.database.assets.get_asset(balance_entry['symbol'])
                amount = deserialize_asset_amount(balance_entry['total'])
                usd_price = balance_entry.get('usdValue', 0)
                assets_balance[asset] = Balance(amount=amount, usd_value=amount * usd_price)
            except UnknownAsset as e:
                self.msg_aggregator.add_warning(f'Unknown asset: {e.identifier}')
                continue

        return assets_balance, ''

    def query_trade_history(self, start_ts: Timestamp, end_ts: Timestamp) -> List[Trade]:
        endpoint = 'user/trades'
        params = {'from': start_ts, 'to': end_ts}
        data = self._make_request(endpoint, params=params)

        trades = []
        for raw_trade in data.get('trades', []):
            try:
                base_asset = self.database.assets.get_asset(raw_trade['baseAsset'])
                quote_asset = self.database.assets.get_asset(raw_trade['quoteAsset'])
                trade_type = TradeType.BUY if raw_trade['side'] == 'buy' else TradeType.SELL
                amount = deserialize_asset_amount(raw_trade['amount'])
                rate = Price(raw_trade['price'])
                fee = Fee(raw_trade['fee'])
                fee_asset = self.database.assets.get_asset(raw_trade['feeAsset'])

                trades.append(Trade(
                    timestamp=Timestamp(raw_trade['timestamp']),
                    location=Location.create('SWYFTX'),
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    trade_type=trade_type,
                    amount=AssetAmount(amount),
                    rate=rate,
                    fee=fee,
                    fee_currency=fee_asset,
                ))
            except UnknownAsset as e:
                self.msg_aggregator.add_warning(f'Unknown asset in trade: {e.identifier}')
                continue

        return trades

    def query_online_history_events(self, start_ts: Timestamp, end_ts: Timestamp) -> List[AssetMovement]:
        deposit_endpoint = 'history/deposit/all'
        withdrawal_endpoint = 'history/withdraw/all'

        deposits = self._make_request(deposit_endpoint, params={'startDate': start_ts, 'endDate': end_ts})
        withdrawals = self._make_request(withdrawal_endpoint, params={'startDate': start_ts, 'endDate': end_ts})

        movements = []

        for raw_movement in deposits.get('items', []):
            movements.append(AssetMovement(
                location=Location.create('SWYFTX'),
                event_type=HistoryEventType.DEPOSIT,
                timestamp=Timestamp(raw_movement['timestamp']),
                asset=self.database.assets.get_asset(raw_movement['assetCode']),
                balance=Balance(amount=deserialize_asset_amount(raw_movement['amount'])),
                unique_id=raw_movement.get('id', None),
                extra_data=None,
            ))

        for raw_movement in withdrawals.get('items', []):
            movements.append(AssetMovement(
                location=Location.create('SWYFTX'),
                event_type=HistoryEventType.WITHDRAWAL,
                timestamp=Timestamp(raw_movement['timestamp']),
                asset=self.database.assets.get_asset(raw_movement['assetCode']),
                balance=Balance(amount=deserialize_asset_amount(raw_movement['amount'])),
                unique_id=raw_movement.get('id', None),
                extra_data=None,
            ))

        return movements

    def validate_api_key(self) -> Tuple[bool, str]:
        try:
            self.query_balances()
            return True, ''
        except RemoteError as e:
            return False, str(e)
