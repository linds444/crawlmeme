-- CrawlMeme memory brain (Phase 11, brought forward for Phase 7): every finished scan, normalised, so later scans can
-- see the same wallets, funders and deployers across tokens.
--
-- Access: only the server, with a Supabase secret key (service_role). Row level security is on for every table with no
-- policies, and anon / authenticated get no privileges on the tables or the two functions: the publishable key can
-- read or write nothing here.
--
-- Two calls per scan, both through PostgREST RPC: memory_lookup (before the verdict) and memory_record (after it).

-- ---------------------------------------------------------------------------------------------------------------
-- tables
-- ---------------------------------------------------------------------------------------------------------------
create table public.tokens (
  chain          text        not null,
  token          text        not null,
  ticker         text,
  name           text,
  launchpad      text,
  deployer       text,
  first_scan_at  timestamptz not null default now(),
  last_scan_at   timestamptz not null default now(),
  last_band      text,
  last_score     integer,
  scans          integer     not null default 0,
  primary key (chain, token)
);
create index tokens_deployer on public.tokens (chain, deployer) where deployer is not null;

create table public.scans (
  id                      bigint      generated always as identity primary key,
  chain                   text        not null,
  token                   text        not null,
  scanned_at              timestamptz not null default now(),
  band                    text,
  score                   integer,
  coverage                text,
  holders_total           integer,
  largest_operator_share  double precision,
  model                   text        not null,
  rug                     jsonb,
  foreign key (chain, token) references public.tokens (chain, token) on delete cascade
);
create index scans_token on public.scans (chain, token, scanned_at desc);

create table public.wallets (
  chain          text        not null,
  address        text        not null,
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),
  primary key (chain, address)
);

-- a wallet among a scan's analysed (top) holders
create table public.holdings (
  scan_id   bigint           not null references public.scans (id) on delete cascade,
  chain     text             not null,
  wallet    text             not null,
  rank      integer          not null,
  share     double precision,
  entry     text,
  flags     text[]           not null default '{}',
  operator  integer,
  funder    text,
  primary key (scan_id, wallet),
  foreign key (chain, wallet) references public.wallets (chain, address) on delete cascade
);
create index holdings_wallet on public.holdings (chain, wallet);
create index holdings_funder on public.holdings (chain, funder) where funder is not null;

-- a wallet's first native deposit before it entered: a historical fact, written once
create table public.funding (
  chain        text             not null,
  wallet       text             not null,
  funder       text             not null,
  block        bigint,
  tx           text,
  amount       double precision,
  observed_at  timestamptz      not null default now(),
  primary key (chain, wallet)
);
create index funding_funder on public.funding (chain, funder);

-- hub check of a funder (outgoing native transfers, capped): refreshed whenever a scan measures it
create table public.funder_stats (
  chain       text        not null,
  address     text        not null,
  outgoing    integer,
  hub         boolean,
  checked_at  timestamptz not null default now(),
  primary key (chain, address)
);

-- evidence edges of a scan's relationship graph (crawler/graph.py)
create table public.edges (
  scan_id     bigint           not null references public.scans (id) on delete cascade,
  chain       text             not null,
  a           text             not null,
  b           text             not null,
  kind        text             not null,
  level       text,
  confidence  double precision,
  tx          text,
  block       bigint,
  primary key (scan_id, a, b, kind)
);
create index edges_a on public.edges (chain, a);
create index edges_b on public.edges (chain, b);

-- ---------------------------------------------------------------------------------------------------------------
-- lock down: server only
-- ---------------------------------------------------------------------------------------------------------------
alter table public.tokens        enable row level security;
alter table public.scans         enable row level security;
alter table public.wallets       enable row level security;
alter table public.holdings      enable row level security;
alter table public.funding       enable row level security;
alter table public.funder_stats  enable row level security;
alter table public.edges         enable row level security;
revoke all on public.tokens, public.scans, public.wallets, public.holdings, public.funding, public.funder_stats,
              public.edges from anon, authenticated;

-- ---------------------------------------------------------------------------------------------------------------
-- memory_record: one finished scan, in one transaction. Returns the scan id.
-- p: {chain, token, ticker, name, launchpad, deployer, band, score, coverage, holders_total, largest_operator_share,
--     model, rug, holders: [{wallet, rank, share, entry, flags, operator, funder}],
--     funding: [{wallet, funder, block, tx, amount}], funders: [{address, outgoing, hub}],
--     edges: [{a, b, kind, level, confidence, tx, block}]}
-- ---------------------------------------------------------------------------------------------------------------
create function public.memory_record(p jsonb) returns bigint
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

  insert into public.scans (chain, token, band, score, coverage, holders_total, largest_operator_share, model, rug)
  values (c, t, p->>'band', (p->>'score')::integer, p->>'coverage', (p->>'holders_total')::integer,
          (p->>'largest_operator_share')::double precision, coalesce(p->>'model', 'unknown'), p->'rug')
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

-- ---------------------------------------------------------------------------------------------------------------
-- memory_lookup: what memory knows about a scan's top holders, their funders and the deployer — from OTHER tokens
-- only (a token's own earlier scans never count as evidence about it).
-- Returns {seen: {wallet: {tokens, danger, list[≤8]}}, pairs: [{a, b, together, tokens}],
--          funders: {funder: {tokens, wallets, token_list}}, funder_stats: {address: {outgoing, hub}},
--          deployer: {tokens, list[≤10]}, memory: {tokens, scans}}
-- ---------------------------------------------------------------------------------------------------------------
create function public.memory_lookup(p_chain text, p_token text, p_wallets text[], p_funders text[],
                                     p_deployer text)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
with hits as (            -- latest scan of each other token where a current holder was a top holder
  select distinct on (h.wallet, s.token) h.wallet, s.token, s.band, s.score, s.scanned_at, h.rank, h.share
  from public.holdings h
  join public.scans s on s.id = h.scan_id
  where h.chain = p_chain and h.wallet = any (p_wallets) and s.token <> p_token
  order by h.wallet, s.token, s.scanned_at desc
),
seen as (
  select g.wallet, count(*) as tokens, count(*) filter (where g.band = 'DANGER') as danger,
         (select jsonb_agg(jsonb_build_object('token', x.token, 'ticker', k.ticker, 'band', x.band, 'score', x.score,
                                              'at', x.scanned_at, 'rank', x.rank, 'share', x.share)
                           order by x.scanned_at desc)
          from (select * from hits x2 where x2.wallet = g.wallet order by x2.scanned_at desc limit 8) x
          left join public.tokens k on k.chain = p_chain and k.token = x.token) as list
  from hits g
  group by g.wallet
),
pairs as (                -- two current top holders that were top holders of the same other token
  select a.wallet as a, b.wallet as b, count(*) as together, jsonb_agg(a.token order by a.token) as tokens
  from hits a
  join hits b on b.token = a.token and a.wallet < b.wallet
  group by a.wallet, b.wallet
),
fund as (                 -- current funders that funded top holders of other tokens
  select f.funder, count(distinct s.token) as tokens, count(distinct f.wallet) as wallets,
         jsonb_agg(distinct s.token) as token_list
  from public.funding f
  join public.holdings h on h.chain = f.chain and h.wallet = f.wallet
  join public.scans s on s.id = h.scan_id and s.token <> p_token
  where f.chain = p_chain and f.funder = any (p_funders)
  group by f.funder
),
dep as (
  select count(*) as n,
         jsonb_agg(jsonb_build_object('token', token, 'ticker', ticker, 'band', last_band, 'score', last_score,
                                      'at', first_scan_at) order by first_scan_at desc) as list
  from (select * from public.tokens
        where chain = p_chain and p_deployer is not null and deployer = p_deployer and token <> p_token
        order by first_scan_at desc limit 10) d
)
select jsonb_build_object(
  'seen',         coalesce((select jsonb_object_agg(wallet, jsonb_build_object('tokens', tokens, 'danger', danger,
                                                                                'list', list)) from seen), '{}'::jsonb),
  'pairs',        coalesce((select jsonb_agg(jsonb_build_object('a', a, 'b', b, 'together', together,
                                                                'tokens', tokens)) from pairs), '[]'::jsonb),
  'funders',      coalesce((select jsonb_object_agg(funder, jsonb_build_object('tokens', tokens, 'wallets', wallets,
                                                                                'token_list', token_list)) from fund),
                           '{}'::jsonb),
  'funder_stats', coalesce((select jsonb_object_agg(address, jsonb_build_object('outgoing', outgoing, 'hub', hub))
                            from public.funder_stats where chain = p_chain and address = any (p_funders)),
                           '{}'::jsonb),
  'deployer',     (select jsonb_build_object('tokens', n, 'list', coalesce(list, '[]'::jsonb)) from dep),
  'memory',       (select jsonb_build_object('tokens', count(*), 'scans', coalesce(sum(scans), 0))
                   from public.tokens where chain = p_chain)
);
$$;

revoke all on function public.memory_record(jsonb) from public, anon, authenticated;
revoke all on function public.memory_lookup(text, text, text[], text[], text) from public, anon, authenticated;
grant execute on function public.memory_record(jsonb) to service_role;
grant execute on function public.memory_lookup(text, text, text[], text[], text) to service_role;
grant select, insert, update on public.tokens, public.scans, public.wallets, public.holdings, public.funding,
                                public.funder_stats, public.edges to service_role;
