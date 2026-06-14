-- Add reservation/limited-space flag to events
alter table events
  add column if not exists requires_reservation boolean default false,
  add column if not exists reservation_note text;
