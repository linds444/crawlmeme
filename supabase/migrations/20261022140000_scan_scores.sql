-- Phase 8: every scoring model's verdict for each scan (live and shadow), so a shadow model can be compared with the
-- live one on real outcomes before it is promoted. {model id: {score, band, adjustments: [code]}}
alter table public.scans add column scores jsonb;

create or replace function public.memory_record(p jsonb) returns bigint
language plpgsql
security invoker
set search_path = ''
as $$
declare
  c   text := p->>'chain';
  t   text := p->>'token';
  sid bigint;
begin
  if c is null or t is null then
    raise exception 'memory_record: chain and token are required';
  end if;

  insert into public.tokens as k (chain, token, ticker, name, launchpad, deployer, last_band, last_score, scans)
  values (c, t, p->>'ticker', p->>'name', p->>'launchpad', p->>'deployer', p->>'band', (p->>'score')::integer, 1)
  on conflict (chain, token) do update set
    ticker       = coalesce(excluded.ticker, k.ticker),
    name         = coalesce(excluded.name, k.name),
    launchpad    = coalesce(excluded.launchpad, k.launchpad),
    deployer     = coalesce(k.deployer, excluded.deployer),
    last_band    = excluded.last_band,
    last_score   = excluded.last_score,
    last_scan_at = now(),
    scans        = k.scans + 1;

  insert into public.scans (chain, token, band, score, coverage, holders_total, largest_operator_share, model, rug,
                            scores)
  values (c, t, p->>'band', (p->>'score')::integer, p->>'coverage', (p->>'holders_total')::integer,
          (p->>'largest_operator_share')::double precision, coalesce(p->>'model', 'unknown'), p->'rug', p->'scores')
  returning id into sid;

  insert into public.wallets as w (chain, address)
  select c, h->>'wallet' from jsonb_array_elements(coalesce(p->'holders', '[]'::jsonb)) h
  on conflict (chain, address) do update set last_seen_at = now();

  insert into public.holdings (scan_id, chain, wallet, rank, share, entry, flags, operator, funder)
  select sid, c, h->>'wallet', (h->>'rank')::integer, (h->>'share')::double precision, h->>'entry',
         coalesce(array(select jsonb_array_elements_text(coalesce(h->'flags', '[]'::jsonb))), '{}'),
         (h->>'operator')::integer, h->>'funder'
  from jsonb_array_elements(coalesce(p->'holders', '[]'::jsonb)) h
  on conflict do nothing;

  insert into public.funding (chain, wallet, funder, block, tx, amount)
  select c, f->>'wallet', f->>'funder', (f->>'block')::bigint, f->>'tx', (f->>'amount')::double precision
  from jsonb_array_elements(coalesce(p->'funding', '[]'::jsonb)) f
  where f->>'funder' is not null
  on conflict (chain, wallet) do nothing;

  insert into public.funder_stats as s (chain, address, outgoing, hub)
  select c, f->>'address', (f->>'outgoing')::integer, (f->>'hub')::boolean
  from jsonb_array_elements(coalesce(p->'funders', '[]'::jsonb)) f
  on conflict (chain, address) do update set outgoing = excluded.outgoing, hub = excluded.hub, checked_at = now();

  insert into public.edges (scan_id, chain, a, b, kind, level, confidence, tx, block)
  select sid, c, e->>'a', e->>'b', e->>'kind', e->>'level', (e->>'confidence')::double precision, e->>'tx',
         (e->>'block')::bigint
  from jsonb_array_elements(coalesce(p->'edges', '[]'::jsonb)) e
  on conflict do nothing;

  return sid;
end;
$$;

revoke all on function public.memory_record(jsonb) from public, anon, authenticated;
grant execute on function public.memory_record(jsonb) to service_role;
