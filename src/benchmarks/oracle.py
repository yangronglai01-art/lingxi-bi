"""Raw-record Python reference arithmetic; never executes or parses SQL."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP


def bounds(month):
    y,m=map(int,month.split('-'));return f'{y:04}-{m:02}-01',f'{y+(m==12):04}-{m%12+1:02}-01'


def wire(v):
    if isinstance(v,Decimal):return format(v.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'f')
    return v


def percent(num,den):return None if not den else (Decimal(num)*100/Decimal(den)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)


class Oracle:
    def __init__(self,data):
        self.r=data['rows'];self.as_of=data['catalog']['as_of']
        self.products={r['product_id']:r['product_name'] for r in self.r['dim_product']}
        self.devices={r['device_id']:r['product_id'] for r in self.r['dim_device']}
        self.skus={r['sku_id']:r['product_id'] for r in self.r['dim_sku']}
        self.orders={r['order_id']:r for r in self.r['fact_order']}
        self.items={r['item_id']:r for r in self.r['fact_order_item']}
        self.refunds=defaultdict(Decimal)
        for r in self.r['fact_refund']:self.refunds[r['item_id']]+=Decimal(r['amount'])

    def generic(self,spec):
        alias,table=spec['source'];records=[{alias+'.'+k:v for k,v in r.items()} for r in self.r[table]]
        for alias,table,left,right in spec.get('joins',[]):
            index=defaultdict(list)
            for r in self.r[table]:index[r[right]].append(r)
            records=[dict(r,**{alias+'.'+k:v for k,v in other.items()}) for r in records for other in index.get(r[left],[])]
        for ref,op,value in spec.get('filters',[]):
            def keep(r):
                a=r[ref]
                if a is None:return False
                return {'eq':lambda:a==value,'ge':lambda:a>=value,'lt':lambda:a<value}[op]()
            records=[r for r in records if keep(r)]
        if spec.get('project'):
            return [[r[ref] for ref,_ in spec['project']] for r in records]
        groups=defaultdict(list)
        for r in records:groups[tuple(r[ref] for ref,_ in spec.get('group',[]))].append(r)
        if not spec.get('group') and not records:groups[()]=[]
        output=[]
        for key,members in groups.items():
            row=list(key)
            for op,ref,_ in spec['aggregates']:
                if op=='count':v=len(members)
                elif op=='offline_rate':v=percent(sum(r[ref]==0 for r in members),len(members))
                elif op=='sum':
                    values=[r[ref] for r in members if r[ref] is not None]
                    v=sum((Decimal(x) if isinstance(x,str) else x for x in values),0)
                else:raise ValueError('Unknown independent aggregate')
                row.append(wire(v))
            output.append(row)
        return output

    def complex(self,kind,month):
        lo,hi=bounds(month);r=self.r
        if kind in {'net','refund_rate'}:
            totals=defaultdict(lambda:[Decimal(0),Decimal(0)])
            for item in self.items.values():
                o=self.orders[item['order_id']]
                if o['status']=='已支付' and lo<=o['paid_date']<hi:
                    p=self.products[self.skus[item['sku_id']]];totals[p][0]+=Decimal(item['amount']);totals[p][1]+=self.refunds[item['item_id']]
            return [[p,wire(g-f)] if kind=='net' else [p,wire(g),wire(f),wire(percent(f,g))] for p,(g,f) in totals.items()]
        if kind in {'activation','service','activation_gap'}:
            source,field=('fact_service_ticket','created_date') if kind=='service' else ('fact_device_activation','activation_date')
            events=defaultdict(list)
            for e in r[source]:events[e['device_id']].append(date.fromisoformat(e[field]))
            den=defaultdict(set);hits=defaultdict(set);immature=defaultdict(set);seen=set();cutoff=date.fromisoformat(self.as_of)-timedelta(days=30)
            for b in r['bridge_order_device']:
                paid=self.orders[self.items[b['item_id']]['order_id']]['paid_date']
                if not lo<=paid<hi:continue
                paid=date.fromisoformat(paid);p=self.products[self.devices[b['device_id']]];seen.add(p)
                if paid>cutoff:immature[p].add(b['device_id']);continue
                den[p].add(b['device_id'])
                if any(0<=(e-paid).days<=30 for e in events[b['device_id']]):hits[p].add(b['device_id'])
            if kind=='activation_gap':return [[p,len(den[p]),len(hits[p]),len(immature[p]),wire(percent(len(hits[p]),len(den[p])))] for p in seen]
            return [[p,len(ids),len(hits[p]),wire(percent(len(hits[p]),len(ids)))] for p,ids in den.items()]
        if kind=='latest':
            latest={}
            for stock in r['fact_inventory_day']:
                if stock['stock_date']>=hi:continue
                key=stock['sku_id'],stock['warehouse_id']
                if key not in latest or stock['stock_date']>latest[key]['stock_date']:latest[key]=stock
            totals=defaultdict(int)
            for stock in latest.values():totals[self.products[self.skus[stock['sku_id']]]]+=stock['quantity']
            return [[p,v] for p,v in totals.items()]
        if kind=='backlog':
            pos={v['purchase_id']:v for v in r['fact_purchase_order']};names={v['supplier_id']:v['supplier_name'] for v in r['dim_supplier']}
            received=defaultdict(int)
            for receipt in r['fact_receipt']:
                if receipt['receipt_date']<hi:received[receipt['purchase_item_id']]+=receipt['quantity']
            sums=defaultdict(lambda:[0,0])
            for item in r['fact_purchase_item']:
                po=pos[item['purchase_id']]
                if po['purchase_date']<hi:
                    out=sums[names[po['supplier_id']]];out[0]+=item['quantity'];out[1]+=received[item['purchase_item_id']]
            return [[p,o,d,o-d] for p,(o,d) in sums.items()]
        if kind=='yoy':
            previous=f'{int(month[:4])-1}'+month[4:];pl,ph=bounds(previous)
            names={v['channel_id']:v['channel_name'] for v in r['dim_channel']};totals={n:[Decimal(0),Decimal(0)] for n in names.values()}
            for o in self.orders.values():
                if o['status']!='已支付':continue
                key=names[o['channel_id']]
                if lo<=o['paid_date']<hi:totals[key][0]+=Decimal(o['paid_amount'])
                if pl<=o['paid_date']<ph:totals[key][1]+=Decimal(o['paid_amount'])
            return [[p,wire(a),wire(b),wire(percent(a-b,b))] for p,(a,b) in totals.items()]
        if kind=='ytd':
            names={v['channel_id']:v['channel_name'] for v in r['dim_channel']};monthly=defaultdict(Decimal)
            for o in self.orders.values():
                if o['status']=='已支付' and month[:4]+'-01-01'<=o['paid_date']<hi:monthly[names[o['channel_id']],o['paid_date'][:7]]+=Decimal(o['paid_amount'])
            totals=defaultdict(Decimal);out=[]
            for (channel,m),value in sorted(monthly.items()):totals[channel]+=value;out.append([channel,m,wire(totals[channel])])
            return out
        if kind=='top2':
            totals=defaultdict(int)
            for item in self.items.values():
                o=self.orders[item['order_id']]
                if o['status']=='已支付' and lo<=o['paid_date']<hi:totals[self.skus[item['sku_id']]]+=item['quantity']
            return [[self.products[p],n,i+1] for i,(p,n) in enumerate(sorted(totals.items(),key=lambda a:(-a[1],a[0]))[:2])]
        if kind=='repeat':
            counts=defaultdict(int)
            for o in self.orders.values():
                if o['status']=='已支付' and lo<=o['paid_date']<hi:counts[o['customer_id']]+=1
            return [[sum(n>=2 for n in counts.values())]]
        if kind=='payment_gap':
            payments=defaultdict(Decimal)
            for p in r['fact_payment']:payments[p['order_id']]+=Decimal(p['amount'])
            head=paid=Decimal(0)
            for o in self.orders.values():
                if o['status']=='已支付' and lo<=o['paid_date']<hi:head+=Decimal(o['paid_amount']);paid+=payments[o['order_id']]
            return [[wire(head),wire(paid),wire(head-paid)]]
        if kind=='marketing_running':
            campaigns={c['campaign_id']:c for c in r['fact_campaign']};monthly=defaultdict(Decimal)
            for v in r['fact_campaign_day']:
                if month[:4]+'-01-01'<=v['event_date']<hi:monthly[self.products[campaigns[v['campaign_id']]['product_id']],v['event_date'][:7]]+=Decimal(v['spend'])
            totals=defaultdict(Decimal);out=[]
            for (p,m),v in sorted(monthly.items()):totals[p]+=v;out.append([p,m,wire(totals[p])])
            return out
        raise ValueError('Unknown independent complex calculation')
