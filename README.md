# mcp_project — Solana Paper Terminal

A local, browser-based Solana trading simulator with an optional MCP bridge for assistant control. All orders are simulated. The app does not create/import wallets, sign transactions, or submit on-chain orders.

## What is included

- Spot paper buys/sells using public indicative DexScreener prices.
- Meme watchlist screening (only tokens added to the watchlist; no automated launch monitoring or token safety audit).
- Leveraged paper long/short positions using spot marks and a simplified liquidation estimate.
- Local public-wallet address list; it does not track/copy wallet transactions.
- Local paper balance, P&L, fees/slippage assumptions, risk limits, and trade ledger.
- MCP tools to inspect the simulated portfolio, submit simulated spot buys/sells, and change the paper trade size cap.

## Run the MCP bridge and app together

The MCP bridge serves the dashboard and MCP stdio server from one process. Use Python 3.10+.

1. Create and activate a virtual environment in this project folder.
2. Install the MCP dependency:

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Add an MCP server entry to the configuration of your MCP-compatible client. Replace `/absolute/path/to/this/project` with the actual absolute project directory:

   ```json
   {
     "mcpServers": {
       "solana-paper-terminal": {
         "command": "python",
         "args": ["/absolute/path/to/this/project/server.py"],
         "env": {"PAPER_TRADE_PORT": "8765"}
       }
     }
   }
   ```

   On Windows, `command` may be an absolute Python executable path, and the script path must use the appropriate absolute path, e.g. `C:\\path\\to\\project\\server.py`.

4. Restart the MCP client. It launches the server as a local child process. Open **http://127.0.0.1:8765** in a browser on the same computer. The page should show a local assistant-bridge notice once connected.
5. Allow the browser to access `https://api.dexscreener.com` for read-only prices.

Do not expose port 8765 on a LAN or public interface. The Python server binds only to `127.0.0.1`, rejects browser requests with a non-local Host/Origin, and has no authentication because it is intended only for a trusted local MCP client. A local process/user with access to the machine may still be able to control the simulation. Do not use this bridge as a remote trading API.

### Available MCP tools

- `get_paper_portfolio`: read simulated balances, prices, positions, P&L, risk level, and recent fills.
- `paper_buy(symbol, amount_usd, confirm=false)`: simulate a spot buy. Requires a recent market price, available simulated cash, and the paper risk cap. Set `confirm=true` only after explicitly requesting the paper trade.
- `paper_sell(symbol, amount_usd, confirm=false)`: simulate selling approximately this USD value of the held paper position, with `confirm=true` after the explicit request.
- `set_paper_risk_level(level, confirm=false)`: set the per-entry paper size cap to 1–20% of simulated equity; requires confirmation.

The bridge does not expose a tool for real trades. Unknown actions are rejected. Paper commands expire if the page is closed or does not respond quickly. The browser stores the account in local storage; use **Reset paper account** to clear it.

## Offline dashboard only

You may open `index.html` directly, but MCP control requires `server.py` to be running and the dashboard opened from the local server URL above. If the bridge runs but the browser is opened from `file://`, browser security may block the bridge connection.

## Important limitations

- Prices are third-party market data and are not executable quotes.
- Paper fills simplify route price impact, network fees, priority fees, MEV, partial fills, and latency.
- Perpetuals do not use an exchange API, orderbook, funding rates, or actual liquidation rules. Liquidation logic is illustrative only.
- Meme screening is not a contract audit or rug-pull detector.
- This is not investment advice and does not imply a strategy is profitable.
