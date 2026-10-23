-- Phase 11: the persistent memory brain. Extends the Phase 7 memory (tokens, scans, wallets, holdings, funding,
-- funder_stats, edges) with launches, market snapshots and later outcomes, operator clusters, launch bundles and their
-- participants, wallet trade events, evidence-backed observations, entity links between deployers (dev groups), and
-- the confidence of every verdict.
--
-- Rules that keep the history honest (docs/phase-11-memory.md):
--   * every read is "as of" a time: only rows that existed before it count (no future data in a verdict or a backtest);
--   * outcome labels come from market data measured after a scan (price and liquidity at a horizon), never from our
--     own verdicts (no circular self-labelling); every scanned token gets them, whatever its verdict (no survivorship);
--   * a token's own history never counts as evidence about it.
--
-- Storage choices: plain Postgres. Price snapshots are a few rows per scan, so a btree on (chain, token, time) plus a
-- BRIN on time is enough (TimescaleDB is not justified at this volume). Dev groups are connected components over
-- entity_links, found with a bounded recursive query (no graph store needed).
--
-- Access: unchanged — row level security on with no policies; only service_role executes the functions.

-- ---------------------------------------------------------------------------------------------------------------
-- new columns
-- ---------------------------------------------------------------------------------------------------------------
alter table public.scans add column confidence text;
alter table public.scans add column data_health jsonb;
alter table public.scans add column market jsonb;
alter table public.holdings add column pnl_usd double precision;
alter table public.holdings add column cost_usd double precision;
alter table public.holdings add column hold_h double precision;
alter table public.holdings add column bundle text;          -- launch bundle kind when the holder was a participant

-- ---------------------------------------------------------------------------------------------------------------
-- tables
-- ---------------------------------------------------------------------------------------------------------------
create table public.launches (
  chain        text        not null,
  token        text        not null,
  block        bigint,
  launched_at  timestamptz,
  tx           text,
  deployer     text,
  launchpad    text,
  recorded_at  timestamptz not null default now(),
  primary key (chain, token),
  foreign key (chain, token) references public.tokens (chain, token) on delete cascade
);
create index launches_deployer on public.launches (chain, deployer) where deployer is not null;

create table public.market_snapshots (
  id             bigint           generated always as identity primary key,
  chain          text             not null,
  token          text             not null,
  observed_at    timestamptz      not null default now(),
  source         text,
  price_usd      double precision,
  mcap_usd       double precision,
  fdv_usd        double precision,
  liquidity_usd  double precision,
  scan_id        bigint           references public.scans (id) on delete set null
);
create index market_snapshots_token on public.market_snapshots (chain, token, observed_at desc);
create index market_snapshots_time on public.market_snapshots using brin (observed_at);

-- what happened to a scanned token after the scan: one row per (scan, horizon), measured by the outcome worker
create table public.outcomes (
  scan_id          bigint           not null references public.scans (id) on delete cascade,
  horizon_h        integer          not null,
  chain            text             not null,
  token            text             not null,
  due_at           timestamptz      not null,
  measured_at      timestamptz,
  elapsed_h        double precision,
  price_ratio      double precision,       -- price at measurement / price at scan
  liquidity_ratio  double precision,
  label            text,                   -- rugged | dead | survived | up | unpriced
  basis            jsonb,
  primary key (scan_id, horizon_h)
);
create index outcomes_due on public.outcomes (due_at) where measured_at is null;
create index outcomes_token on public.outcomes (chain, token, measured_at);

create table public.operators (
  scan_id   bigint           not null references public.scans (id) on delete cascade,
  chain     text             not null,
  idx       integer          not null,
  wallets   text[]           not null,
  level     text,
  share     double precision,
  primary key (scan_id, idx)
);
create index operators_wallets on public.operators using gin (wallets);

create table public.bundles (
  scan_id             bigint           primary key references public.scans (id) on delete cascade,
  chain               text             not null,
  token               text             not null,
  mode                text,
  confidence          text,
  participants        integer,
  initial_share       double precision,
  traceable_share     double precision,
  concentrated_share  double precision,
  sold_share          double precision,
  fresh_recipients    integer
);

create table public.bundle_participants (
  scan_id         bigint           not null references public.scans (id) on delete cascade,
  chain           text             not null,
  wallet          text             not null,
  kind            text             not null,     -- buy | dev_transfer | transfer | deployer | sniper
  acquired_share  double precision,
  held_share      double precision,
  sold_share      double precision,
  status          text,
  tx              text,
  primary key (scan_id, wallet)
);
create index bundle_participants_wallet on public.bundle_participants (chain, wallet);

-- token trades and transfers of analysed holders (from the scan's own transfer history)
create table public.wallet_events (
  scan_id  bigint      not null references public.scans (id) on delete cascade,
  chain    text        not null,
  wallet   text        not null,
  token    text        not null,
  kind     text        not null,               -- buy | sell | in | out
  block    bigint,
  tx       text,
  amount   numeric,
  primary key (scan_id, wallet, tx, kind)
);
create index wallet_events_wallet on public.wallet_events (chain, wallet);

-- behavioural observations with their evidence. confirmed: backed by an on-chain fact (a shared transaction, a direct
-- transfer, a funding transfer, a launch transaction); unconfirmed: statistical (packs, co-holding, timing)
create table public.observations (
  id           bigint           generated always as identity primary key,
  chain        text             not null,
  subject      text             not null,
  subject_kind text             not null,      -- wallet | deployer | funder | token
  kind         text             not null,
  status       text             not null check (status in ('confirmed', 'unconfirmed')),
  confidence   double precision,
  evidence     jsonb            not null,
  scan_id      bigint           references public.scans (id) on delete cascade,
  token        text,
  observed_at  timestamptz      not null default now()
);
create index observations_subject on public.observations (chain, subject, observed_at);

-- links between entities (deployers) found by entity resolution; a dev group is a connected component
create table public.entity_links (
  chain          text             not null,
  a              text             not null,
  b              text             not null,
  kind           text             not null,      -- shared_bundle_wallets | shared_funder
  confidence     double precision,
  evidence       jsonb            not null,
  first_seen_at  timestamptz      not null default now(),
  last_seen_at   timestamptz      not null default now(),
  primary key (chain, a, b, kind),
  check (a < b)
);
create index entity_links_b on public.entity_links (chain, b);

alter table public.launches            enable row level security;
alter table public.market_snapshots    enable row level security;
alter table public.outcomes            enable row level security;
alter table public.operators           enable row level security;
alter table public.bundles             enable row level security;
alter table public.bundle_participants enable row level security;
alter table public.wallet_events       enable row level security;
alter table public.observations        enable row level security;
alter table public.entity_links        enable row level security;
revoke all on public.launches, public.market_snapshots, public.outcomes, public.operators, public.bundles,
              public.bundle_participants, public.wallet_events, public.observations, public.entity_links
  from anon, authenticated;
grant select, insert, update on public.launches, public.market_snapshots, public.outcomes, public.operators,
                                public.bundles, public.bundle_participants, public.wallet_events, public.observations,
                                public.entity_links to service_role;

-- ---------------------------------------------------------------------------------------------------------------
-- memory_record: the whole scan in one transaction (Phase 7 parts unchanged, Phase 11 parts added)
-- ---------------------------------------------------------------------------------------------------------------
create or replace function public.memory_record(p jsonb) returns bigint
language plpgsql
security invoker
set search_path = ''
as $$
declare
  c   text := p->>'chain';
  t   text := p->>'token';
  dep text := p->>'deployer';
  sid bigint;
  hz  integer;
begin
  if c is null or t is null then
    raise exception 'memory_record: chain and token are required';
  end if;

  insert into public.tokens as k (chain, token, ticker, name, launchpad, deployer, last_band, last_score, scans)
  values (c, t, p->>'ticker', p->>'name', p->>'launchpad', dep, p->>'band', (p->>'score')::integer, 1)
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
                            scores, confidence, data_health, market)
  values (c, t, p->>'band', (p->>'score')::integer, p->>'coverage', (p->>'holders_total')::integer,
          (p->>'largest_operator_share')::double precision, coalesce(p->>'model', 'unknown'), p->'rug', p->'scores',
          p->>'confidence', p->'data_health', p->'market')
  returning id into sid;

  insert into public.wallets as w (chain, address)
  select c, h->>'wallet' from jsonb_array_elements(coalesce(p->'holders', '[]'::jsonb)) h
  on conflict (chain, address) do update set last_seen_at = now();

  insert into public.holdings (scan_id, chain, wallet, rank, share, entry, flags, operator, funder, pnl_usd, cost_usd,
                               hold_h, bundle)
  select sid, c, h->>'wallet', (h->>'rank')::integer, (h->>'share')::double precision, h->>'entry',
         coalesce(array(select jsonb_array_elements_text(coalesce(h->'flags', '[]'::jsonb))), '{}'),
         (h->>'operator')::integer, h->>'funder', (h->>'pnl_usd')::double precision,
         (h->>'cost_usd')::double precision, (h->>'hold_h')::double precision, h->>'bundle'
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

  -- Phase 11 ----------------------------------------------------------------------------------------------------
  if p ? 'launch' and p->'launch' <> 'null'::jsonb then
    insert into public.launches (chain, token, block, launched_at, tx, deployer, launchpad)
    values (c, t, (p->'launch'->>'block')::bigint, to_timestamp((p->'launch'->>'ts')::double precision),
            p->'launch'->>'tx', dep, p->>'launchpad')
    on conflict (chain, token) do nothing;
  end if;

  if p ? 'market' and p->'market' <> 'null'::jsonb then
    insert into public.market_snapshots (chain, token, source, price_usd, mcap_usd, fdv_usd, liquidity_usd, scan_id)
    values (c, t, p->'market'->>'source', (p->'market'->>'price_usd')::double precision,
            (p->'market'->>'mcap_usd')::double precision, (p->'market'->>'fdv_usd')::double precision,
            (p->'market'->>'liquidity_usd')::double precision, sid);
    -- outcome horizons for this scan (the worker measures them later); every verdict gets them
    if (p->'market'->>'price_usd') is not null then
      foreach hz in array array[24, 168] loop   -- one pending measurement per token and horizon per 6 hours
        insert into public.outcomes (scan_id, horizon_h, chain, token, due_at)
        select sid, hz, c, t, now() + make_interval(hours => hz)
        where not exists (select 1 from public.outcomes o2
                          where o2.chain = c and o2.token = t and o2.horizon_h = hz and o2.measured_at is null
                            and o2.due_at > now() + make_interval(hours => hz) - interval '6 hours');
      end loop;
    end if;
  end if;

  insert into public.operators (scan_id, chain, idx, wallets, level, share)
  select sid, c, (o->>'idx')::integer, array(select jsonb_array_elements_text(o->'wallets')), o->>'level',
         (o->>'share')::double precision
  from jsonb_array_elements(coalesce(p->'operators', '[]'::jsonb)) o
  on conflict do nothing;

  if p ? 'bundle' and p->'bundle' <> 'null'::jsonb then
    insert into public.bundles (scan_id, chain, token, mode, confidence, participants, initial_share, traceable_share,
                                concentrated_share, sold_share, fresh_recipients)
    values (sid, c, t, p->'bundle'->>'mode', p->'bundle'->>'confidence', (p->'bundle'->>'participants')::integer,
            (p->'bundle'->>'initial_share')::double precision, (p->'bundle'->>'traceable_share')::double precision,
            (p->'bundle'->>'concentrated_share')::double precision, (p->'bundle'->>'sold_share')::double precision,
            (p->'bundle'->>'fresh_recipients')::integer);
    insert into public.bundle_participants (scan_id, chain, wallet, kind, acquired_share, held_share, sold_share,
                                            status, tx)
    select sid, c, b->>'wallet', b->>'kind', (b->>'acquired_share')::double precision,
           (b->>'held_share')::double precision, (b->>'sold_share')::double precision, b->>'status', b->>'tx'
    from jsonb_array_elements(coalesce(p->'bundle'->'list', '[]'::jsonb)) b
    on conflict do nothing;
  end if;

  insert into public.wallet_events (scan_id, chain, wallet, token, kind, block, tx, amount)
  select sid, c, e->>'wallet', t, e->>'kind', (e->>'block')::bigint, e->>'tx', (e->>'amount')::numeric
  from jsonb_array_elements(coalesce(p->'events', '[]'::jsonb)) e
  where e->>'tx' is not null
  on conflict do nothing;

  insert into public.observations (chain, subject, subject_kind, kind, status, confidence, evidence, scan_id, token)
  select c, o->>'subject', o->>'subject_kind', o->>'kind', o->>'status', (o->>'confidence')::double precision,
         o->'evidence', sid, t
  from jsonb_array_elements(coalesce(p->'observations', '[]'::jsonb)) o;

  -- entity resolution: link this deployer to other deployers whose tokens' launch bundles share 2+ wallets with this
  -- one (the same wallets taking supply at several launches), with the shared wallets and tokens as evidence
  if dep is not null then
    insert into public.entity_links as l (chain, a, b, kind, confidence, evidence)
    select c, least(dep, x.deployer), greatest(dep, x.deployer), 'shared_bundle_wallets',
           least(1.0, 0.4 + 0.15 * x.n),
           jsonb_build_object('wallets', x.wallets, 'tokens', jsonb_build_array(t, x.token), 'scan_id', sid)
    from (
      select k.deployer, s.token, count(distinct bp.wallet) as n, jsonb_agg(distinct bp.wallet) as wallets
      from public.bundle_participants bp
      join public.scans s on s.id = bp.scan_id
      join public.tokens k on k.chain = s.chain and k.token = s.token
      where bp.chain = c and s.token <> t and k.deployer is not null and k.deployer <> dep
        and bp.kind <> 'deployer'
        and bp.wallet in (select b2->>'wallet' from jsonb_array_elements(coalesce(p->'bundle'->'list', '[]'::jsonb)) b2
                          where b2->>'kind' <> 'deployer')
      group by k.deployer, s.token
      having count(distinct bp.wallet) >= 2
    ) x
    on conflict (chain, a, b, kind) do update set last_seen_at = now(),
      confidence = greatest(l.confidence, excluded.confidence);
  end if;

  return sid;
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- memory_lookup v2: everything as of p_as_of (default now), other tokens only. Adds outcome-backed history (labels
-- measured before p_as_of), the deployer's dev group (entity links seen before p_as_of, up to 3 hops) and prior
-- observations about the subjects.
-- ---------------------------------------------------------------------------------------------------------------
drop function if exists public.memory_lookup(text, text, text[], text[], text);
create function public.memory_lookup(p_chain text, p_token text, p_wallets text[], p_funders text[],
                                     p_deployer text, p_as_of timestamptz default now())
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
with past_scans as (
  select * from public.scans s where s.chain = p_chain and s.token <> p_token and s.scanned_at < p_as_of
),
hits as (
  select distinct on (h.wallet, s.token) h.wallet, s.token, s.band, s.score, s.scanned_at, h.rank, h.share
  from public.holdings h
  join past_scans s on s.id = h.scan_id
  where h.chain = p_chain and h.wallet = any (p_wallets)
  order by h.wallet, s.token, s.scanned_at desc
),
labels as (               -- each other token's market outcome known at p_as_of (longest horizon measured by then)
  select distinct on (o.token) o.token, o.label, o.horizon_h, o.measured_at
  from public.outcomes o
  where o.chain = p_chain and o.token <> p_token and o.measured_at is not null and o.measured_at < p_as_of
    and o.label is not null and o.label <> 'unpriced'
  order by o.token, o.horizon_h desc, o.measured_at desc
),
seen as (
  select g.wallet, count(*) as tokens, count(*) filter (where g.band = 'DANGER') as danger,
         count(*) filter (where l.label = 'rugged') as rugged,
         count(l.label) as labelled,
         (select jsonb_agg(jsonb_build_object('token', x.token, 'ticker', k.ticker, 'band', x.band, 'score', x.score,
                                              'at', x.scanned_at, 'rank', x.rank, 'share', x.share,
                                              'outcome', l2.label, 'outcome_at', l2.measured_at)
                           order by x.scanned_at desc)
          from (select * from hits x2 where x2.wallet = g.wallet order by x2.scanned_at desc limit 8) x
          left join public.tokens k on k.chain = p_chain and k.token = x.token
          left join labels l2 on l2.token = x.token) as list
  from hits g
  left join labels l on l.token = g.token
  group by g.wallet
),
pairs as (
  select a.wallet as a, b.wallet as b, count(*) as together, jsonb_agg(a.token order by a.token) as tokens
  from hits a
  join hits b on b.token = a.token and a.wallet < b.wallet
  group by a.wallet, b.wallet
),
fund as (
  select f.funder, count(distinct s.token) as tokens, count(distinct f.wallet) as wallets,
         jsonb_agg(distinct s.token) as token_list
  from public.funding f
  join public.holdings h on h.chain = f.chain and h.wallet = f.wallet
  join past_scans s on s.id = h.scan_id
  where f.chain = p_chain and f.funder = any (p_funders) and f.observed_at < p_as_of
  group by f.funder
),
grp as (                  -- the deployer's dev group: entity links seen before p_as_of, up to 3 hops
  with recursive g(addr, depth) as (
    select p_deployer, 0 where p_deployer is not null
    union
    select case when l.a = g.addr then l.b else l.a end, g.depth + 1
    from g join public.entity_links l
      on l.chain = p_chain and (l.a = g.addr or l.b = g.addr) and l.first_seen_at < p_as_of
    where g.depth < 3
  )
  select distinct addr from g
),
dep_tokens as (
  select k.token, k.ticker, k.deployer, k.first_scan_at,
         (select s.band from past_scans s where s.token = k.token order by s.scanned_at desc limit 1) as band,
         (select s.score from past_scans s where s.token = k.token order by s.scanned_at desc limit 1) as score,
         l.label, l.measured_at
  from public.tokens k
  left join labels l on l.token = k.token
  where k.chain = p_chain and k.token <> p_token and k.deployer in (select addr from grp)
    and k.first_scan_at < p_as_of
),
clusters as (             -- operator clusters of other tokens that contain 2+ of this scan's holders
  select s.token, o.level, o.share, s.scanned_at,
         array(select unnest(o.wallets) intersect select unnest(p_wallets)) as overlap
  from public.operators o
  join past_scans s on s.id = o.scan_id
  where o.chain = p_chain and o.wallets && p_wallets
),
obs as (
  select o.subject, jsonb_agg(jsonb_build_object('kind', o.kind, 'status', o.status, 'confidence', o.confidence,
                                                 'token', o.token, 'at', o.observed_at, 'evidence', o.evidence)
                              order by o.observed_at desc) as list
  from public.observations o
  where o.chain = p_chain and o.token is distinct from p_token and o.observed_at < p_as_of
    and o.subject = any (p_wallets || coalesce(p_funders, '{}') || coalesce(array[p_deployer], '{}'))
  group by o.subject
)
select jsonb_build_object(
  'as_of',        p_as_of,
  'seen',         coalesce((select jsonb_object_agg(wallet, jsonb_build_object('tokens', tokens, 'danger', danger,
                                                                                'rugged', rugged, 'labelled', labelled,
                                                                                'list', list)) from seen), '{}'::jsonb),
  'pairs',        coalesce((select jsonb_agg(jsonb_build_object('a', a, 'b', b, 'together', together,
                                                                'tokens', tokens)) from pairs), '[]'::jsonb),
  'funders',      coalesce((select jsonb_object_agg(funder, jsonb_build_object('tokens', tokens, 'wallets', wallets,
                                                                                'token_list', token_list)) from fund),
                           '{}'::jsonb),
  'funder_stats', coalesce((select jsonb_object_agg(address, jsonb_build_object('outgoing', outgoing, 'hub', hub))
                            from public.funder_stats where chain = p_chain and address = any (p_funders)),
                           '{}'::jsonb),
  'deployer',     (select jsonb_build_object(
                     'tokens', count(*) filter (where deployer = p_deployer),
                     'group_tokens', count(*),
                     'group', (select coalesce(jsonb_agg(addr), '[]'::jsonb) from grp where addr <> p_deployer),
                     'rugged', count(*) filter (where label = 'rugged'),
                     'labelled', count(label),
                     'list', coalesce((select jsonb_agg(jsonb_build_object('token', d.token, 'ticker', d.ticker,
                                                                          'deployer', d.deployer, 'band', d.band,
                                                                          'score', d.score, 'at', d.first_scan_at,
                                                                          'outcome', d.label,
                                                                          'outcome_at', d.measured_at)
                                                       order by d.first_scan_at desc)
                                       from (select * from dep_tokens order by first_scan_at desc limit 12) d),
                                      '[]'::jsonb))
                   from dep_tokens),
  'observations', coalesce((select jsonb_object_agg(subject, list) from obs), '{}'::jsonb),
  'clusters',     coalesce((select jsonb_agg(jsonb_build_object('token', token, 'level', level, 'share', share,
                                                                'wallets', to_jsonb(overlap), 'at', scanned_at)
                                             order by scanned_at desc)
                            from (select * from clusters where cardinality(overlap) >= 2
                                  order by scanned_at desc limit 10) c), '[]'::jsonb),
  'memory',       (select jsonb_build_object('tokens', count(distinct token), 'scans', count(*))
                   from public.scans where chain = p_chain and scanned_at < p_as_of),
  'outcomes',     (select jsonb_build_object('labelled', count(*), 'rugged', count(*) filter (where label = 'rugged'))
                   from labels)
);
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- the outcome worker's two calls
-- ---------------------------------------------------------------------------------------------------------------
create function public.outcomes_due(p_limit integer default 60)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select coalesce(jsonb_agg(jsonb_build_object('scan_id', o.scan_id, 'horizon_h', o.horizon_h, 'chain', o.chain,
                                             'token', o.token, 'due_at', o.due_at, 'scanned_at', s.scanned_at,
                                             'price_usd', m.price_usd, 'liquidity_usd', m.liquidity_usd)
                          order by o.due_at), '[]'::jsonb)
from (select * from public.outcomes where measured_at is null and due_at <= now() order by due_at
      limit greatest(1, least(p_limit, 300))) o
join public.scans s on s.id = o.scan_id
left join lateral (select price_usd, liquidity_usd from public.market_snapshots ms
                   where ms.scan_id = o.scan_id order by ms.observed_at limit 1) m on true;
$$;

create function public.outcome_record(p jsonb) returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
  n integer;
begin
  update public.outcomes o set
    measured_at     = now(),
    elapsed_h       = extract(epoch from (now() - s.scanned_at)) / 3600.0,
    price_ratio     = (r->>'price_ratio')::double precision,
    liquidity_ratio = (r->>'liquidity_ratio')::double precision,
    label           = r->>'label',
    basis           = r->'basis'
  from jsonb_array_elements(coalesce(p->'rows', '[]'::jsonb)) r, public.scans s
  where o.scan_id = (r->>'scan_id')::bigint and o.horizon_h = (r->>'horizon_h')::integer and s.id = o.scan_id
    and o.measured_at is null;
  get diagnostics n = row_count;
  insert into public.market_snapshots (chain, token, source, price_usd, mcap_usd, fdv_usd, liquidity_usd)
  select r->>'chain', r->>'token', r->'basis'->>'source', (r->'basis'->>'price_usd')::double precision,
         (r->'basis'->>'mcap_usd')::double precision, (r->'basis'->>'fdv_usd')::double precision,
         (r->'basis'->>'liquidity_usd')::double precision
  from jsonb_array_elements(coalesce(p->'rows', '[]'::jsonb)) r
  where r->'basis'->>'price_usd' is not null or r->'basis'->>'liquidity_usd' is not null;
  return n;
end;
$$;

revoke all on function public.memory_record(jsonb) from public, anon, authenticated;
revoke all on function public.memory_lookup(text, text, text[], text[], text, timestamptz) from public, anon,
  authenticated;
revoke all on function public.outcomes_due(integer) from public, anon, authenticated;
revoke all on function public.outcome_record(jsonb) from public, anon, authenticated;
grant execute on function public.memory_record(jsonb) to service_role;
grant execute on function public.memory_lookup(text, text, text[], text[], text, timestamptz) to service_role;
grant execute on function public.outcomes_due(integer) to service_role;
grant execute on function public.outcome_record(jsonb) to service_role;
