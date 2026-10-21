"""Record real JSON-RPC traffic of an EVM adapter run into a replay fixture (tests/fixtures/evm/*.json.gz).

  python tests/fixtures/evm/record.py <chain> <launchpad> <token> <name>

Runs detect → get_launch → token_facts → classify_entries for the token's top holders against the configured
providers (public endpoints suffice for fresh tokens), stores every request/response pair keyed by method+params,
plus the head block. tests/test_evm_launchpads.py replays the fixture through EvmClient with no network, so the
plugins are regression-tested on real mainnet data. Provider URLs are never written to the fixture.
"""
import gzip, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from crawler import detect as d                              # noqa: E402
from crawler.chains.evm import networks                      # noqa: E402
from crawler.chains.evm.launchpads import clanker, flap, fourmeme  # noqa: E402
from crawler.chains.evm.token import EvmTokenAdapter          # noqa: E402

PLUGINS = {"clanker": clanker, "fourmeme": fourmeme, "flap": flap}


def key(req):
    return json.dumps([req["method"], req.get("params", [])], sort_keys=True)


def main(chain, pad, token, name):
    client = networks.client(chain)
    real = client.transport
    book = {}

    def recording(url, body, timeout):
        status, raw = real(url, body, timeout)
        if status == 200:
            reqs, resp = json.loads(body), json.loads(raw)
            pairs = zip(reqs, sorted(resp, key=lambda r: r.get("id", 0))) if isinstance(reqs, list) else [(reqs, resp)]
            for q, r in pairs:   # refusals too: the replay must take the same fallback path
                book[key(q)] = {"error": r["error"]} if "error" in r else {"result": r.get("result")}
        return status, raw

    client.transport = recording
    head = client.head()
    client.head = lambda: head      # frozen: every window that ends at the head is keyed by the same block number
    ad = EvmTokenAdapter(networks.NETWORKS[chain], client, PLUGINS[pad])
    assert ad.detect(token) is not None, "not recognised"
    launch = ad.get_launch(token)
    facts = ad.token_facts(token, launch)
    base = d.supply_base(facts["transfers"], facts["supply"], facts["excluded"])
    holders = [w for w, _, _ in d.top_holders(base)]
    ad.classify_entries(token, facts["transfers"], holders)
    ad.block_timestamps([launch["block"]])
    ad.token_meta(token)
    out = {"chain": chain, "launchpad": pad, "token": token, "head": head, "responses": book}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}.json.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), sort_keys=True)
    print(f"{path}: {len(book)} responses, {os.path.getsize(path) // 1024} KB, holders {base['holders_total']}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
