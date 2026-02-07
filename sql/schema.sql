create database inventory;

create table inventory(
	id serial primary key,
	item_name varchar(255) not null,
	category varchar(100),
	location varchar(50) not null
		check (location in ('fridge','freezer','pantry')),
	quantity numeric(10,2) default 1.0,
	unit varchar(20),
	expire_date date,
	created_at timestamp default current_timestamp,
	status varchar(20) default 'in_stock'
		check (status in ('in_stock','consumed','wasted'))
);
