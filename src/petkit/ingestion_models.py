"""Versioned local upstream contract; no arbitrary table or SQL fields."""
from datetime import date
from decimal import Decimal
from typing import Annotated,Literal,Union
from pydantic import BaseModel,ConfigDict,Field,StringConstraints,field_validator,model_validator

Identifier=Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Za-z0-9_.:-]{1,96}$')]
Source=Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Za-z0-9_-]{1,64}$')]

class Record(BaseModel):
    model_config=ConfigDict(extra='forbid')
    event_id:Identifier

    @field_validator('refund_date','activation_date','created_date',mode='before',check_fields=False)
    @classmethod
    def iso_date(cls,value):
        if not isinstance(value,str) or len(value)!=10: raise ValueError('日期必须为YYYY-MM-DD字符串')
        return date.fromisoformat(value)

class Refund(Record):
    kind:Literal['refund.created']
    refund_id:Identifier
    item_id:Identifier
    refund_date:date
    amount:Annotated[str,StringConstraints(strict=True,pattern=r'^(0|[1-9][0-9]{0,8})\.[0-9]{2}$')]

    @field_validator('amount')
    @classmethod
    def positive(cls,value):
        if Decimal(value)<=0: raise ValueError('退款金额必须大于0')
        return value

class Activation(Record):
    kind:Literal['device.activated']
    device_id:Identifier
    activation_date:date

class Ticket(Record):
    kind:Literal['ticket.created']
    ticket_id:Identifier
    device_id:Identifier
    fault_id:Identifier
    employee_id:Identifier
    created_date:date


class OrderDevice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id:Identifier
    manufactured_date:date
    _date=field_validator('manufactured_date',mode='before')(Record.iso_date.__func__)

class OrderItem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    item_id:Identifier
    sku_id:Identifier
    quantity:int=Field(strict=True,ge=1,le=20)
    amount:Annotated[str,StringConstraints(strict=True,pattern=r'^(0|[1-9][0-9]{0,8})\.[0-9]{2}$')]
    devices:list[OrderDevice]=Field(min_length=1,max_length=20)

    @model_validator(mode='after')
    def balanced(self):
        if Decimal(self.amount)<=0 or len(self.devices)!=self.quantity: raise ValueError('订单行金额须为正且设备数须等于数量')
        return self

class PaidOrder(Record):
    kind:Literal['order.paid']
    order_id:Identifier
    customer_id:Identifier
    channel_id:Identifier
    paid_date:date
    paid_amount:Annotated[str,StringConstraints(strict=True,pattern=r'^(0|[1-9][0-9]{0,8})\.[0-9]{2}$')]
    items:list[OrderItem]=Field(min_length=1,max_length=20)
    _date=field_validator('paid_date',mode='before')(Record.iso_date.__func__)

    @model_validator(mode='after')
    def balanced(self):
        ids=[i.item_id for i in self.items]
        devices=[d.device_id for i in self.items for d in i.devices]
        if len(set(ids))!=len(ids) or len(set(devices))!=len(devices): raise ValueError('订单内明细和设备ID不能重复')
        if sum(Decimal(i.amount) for i in self.items)!=Decimal(self.paid_amount): raise ValueError('实付与明细金额不平')
        if any(d.manufactured_date>self.paid_date for i in self.items for d in i.devices): raise ValueError('生产日期晚于支付日期')
        return self

class Shipment(Record):
    kind:Literal['order.shipped']
    shipment_id:Identifier
    order_id:Identifier
    warehouse_id:Identifier
    ship_date:date
    _date=field_validator('ship_date',mode='before')(Record.iso_date.__func__)

class PurchaseItem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    purchase_item_id:Identifier
    sku_id:Identifier
    quantity:int=Field(strict=True,ge=1,le=10000)
    amount:Annotated[str,StringConstraints(strict=True,pattern=r'^(0|[1-9][0-9]{0,8})\.[0-9]{2}$')]

    @field_validator('amount')
    @classmethod
    def positive(cls,value):
        if Decimal(value)<=0: raise ValueError('采购金额必须大于0')
        return value

class Purchase(Record):
    kind:Literal['purchase.created']
    purchase_id:Identifier
    supplier_id:Identifier
    purchase_date:date
    items:list[PurchaseItem]=Field(min_length=1,max_length=20)
    _date=field_validator('purchase_date',mode='before')(Record.iso_date.__func__)

    @model_validator(mode='after')
    def distinct(self):
        if len({i.purchase_item_id for i in self.items})!=len(self.items): raise ValueError('采购行ID不能重复')
        return self

class Receipt(Record):
    kind:Literal['purchase.received']
    receipt_id:Identifier
    purchase_item_id:Identifier
    warehouse_id:Identifier
    receipt_date:date
    quantity:int=Field(strict=True,ge=1,le=10000)
    _date=field_validator('receipt_date',mode='before')(Record.iso_date.__func__)

Event=Annotated[Union[Refund,Activation,Ticket,PaidOrder,Shipment,Purchase,Receipt],Field(discriminator='kind')]

class Batch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:Literal[1]
    source:Source
    batch_id:Source
    events:list[Event]=Field(min_length=1,max_length=1000)

    @field_validator('version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int or value!=1: raise ValueError('version必须为整数1')
        return value

    @field_validator('events')
    @classmethod
    def distinct_ids(cls,values):
        if sum(i.quantity for e in values if isinstance(e,PaidOrder) for i in e.items)>1000: raise ValueError('单批新增设备不得超过1000')
        if len({v.event_id for v in values})!=len(values): raise ValueError('批次中event_id不能重复')
        return values
