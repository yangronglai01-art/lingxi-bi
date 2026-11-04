"""Validate business units using Decimal after wire conversion, without rounding money."""
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP


def validate(metric,result):
    columns=result['columns'];rows=result['rows'];kind=metric['id']
    if len(set(columns))!=len(columns) or any(len(row)!=len(columns) for row in rows):raise ValueError('Ambiguous or malformed output')
    def number(value):
        if isinstance(value,bool) or value is None:raise ValueError('Invalid numeric output')
        try:d=Decimal(str(value))
        except InvalidOperation:raise ValueError('Invalid numeric output') from None
        if not d.is_finite():raise ValueError('Nonfinite numeric output')
        return d
    if kind in {'net_sales','inventory','tickets'}:
        alias={'net_sales':'net_sales','inventory':'stock_quantity','tickets':'tickets'}[kind]
        if columns.count(alias)!=1:raise ValueError('Approved metric alias required')
        for row in rows:
            v=number(row[columns.index(alias)])
            if kind!='net_sales' and v!=v.to_integral_value():raise ValueError('Count output must be integral')
            if kind=='tickets' and v<0:raise ValueError('Ticket count cannot be negative')
    if kind=='offline_rate':
        if any(columns.count(c)!=1 for c in ('device_days','offline_percent')):raise ValueError('Offline output aliases required')
        for row in rows:
            den=number(row[columns.index('device_days')]);rate=row[columns.index('offline_percent')]
            if den<0 or den!=den.to_integral_value():raise ValueError('Invalid device-days')
            if den==0:
                if rate is not None:raise ValueError('Zero denominator requires NULL')
            elif not 0<=number(rate)<=100:raise ValueError('Percentage outside range')
    if kind=='received_units':
        if columns.count('received_units')!=1:raise ValueError('Received unit alias required')
        i=columns.index('received_units')
        for row in rows:
            v=number(row[i])
            if v<0 or v!=v.to_integral_value():raise ValueError('Received units must be nonnegative integers')
    if kind in {'activation30','service30'}:
        fields=['devices','matched_devices','rate_percent']
        if any(columns.count(f)!=1 for f in fields):raise ValueError('Cohort output aliases required')
        indexes=[columns.index(f) for f in fields]
        for row in rows:
            den,num=map(number,[row[indexes[0]],row[indexes[1]]]);rate=row[indexes[2]]
            if den<0 or num<0 or num>den or any(v!=v.to_integral_value() for v in (den,num)):
                raise ValueError('Invalid cohort counts')
            if den==0:
                if rate is not None:raise ValueError('Zero denominator requires NULL')
            elif abs(number(rate)-(100*num/den).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))>Decimal('.011'):
                raise ValueError('Cohort percentage unit mismatch')
