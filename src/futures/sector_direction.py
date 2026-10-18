"""Shared absolute direction confirmation, additional to relative-strength checks."""
from math import isfinite


def alignment(side, stock_return, sector_return):
    """Session returns use the same completed intraday window, from session open."""
    if side not in ('LONG', 'SHORT'):
        raise ValueError('INVALID_DIRECTION')
    if not all(isfinite(value) for value in (stock_return, sector_return)):
        raise ValueError('SECTOR_DIRECTION_UNAVAILABLE')
    sign = 1 if side == 'LONG' else -1
    reasons = []
    if sector_return * sign <= 0:
        reasons.append(side + '_SECTOR_NOT_' + ('BULLISH' if side == 'LONG' else 'BEARISH'))
    if stock_return * sign <= 0:
        reasons.append(side + '_STOCK_NOT_' + ('BULLISH' if side == 'LONG' else 'BEARISH'))
    return {'status': 'BLOCKED' if reasons else 'PASS', 'reason_codes': reasons,
            'basis': 'SESSION_OPEN_TO_LATEST_COMPLETED_CLOSE',
            'stock_return_percent': stock_return, 'sector_return_percent': sector_return}
