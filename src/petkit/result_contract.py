"""Stable business output units for managed thirty-day cohort metrics."""
import math

def validate(metric,result):
    if metric and metric['id']=='received_units':
        from .semantic import MetricError
        if result.columns.count('received_units')!=1: raise MetricError('收货数量输出列别名必须为received_units，单位为件')
        index=result.columns.index('received_units')
        if any(not isinstance(row[index],(int,float)) or isinstance(row[index],bool) or not math.isfinite(row[index]) or row[index]<0 or int(row[index])!=row[index] for row in result.rows): raise MetricError('收货数量必须是非负整数件数')
        return
    if not metric or metric['id'] not in {'activation30','service30'}: return
    from .semantic import MetricError
    fields=['devices','matched_devices','rate_percent']
    if not all(result.columns.count(f)==1 for f in fields):
        raise MetricError('30天指标输出契约：设备分母列别名devices，分子matched_devices，百分数rate_percent（必须乘100），并返回型号名称；禁止用0到1小数冒充百分数。')
    indexes=[result.columns.index(f) for f in fields]
    for row in result.rows:
        den,num,rate=[row[i] for i in indexes]
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0 and int(v)==v for v in [den,num]) or num>den:
            raise MetricError('30天指标分子分母必须是非负设备计数，且分子不能大于分母')
        if den==0:
            if rate is not None: raise MetricError('零分母比率应返回NULL')
        elif not isinstance(rate,(int,float)) or not math.isfinite(rate) or abs(rate-round(100*num/den,2))>.011:
            raise MetricError('rate_percent必须为100*matched_devices/devices的百分数，请修复单位或聚合公式')
