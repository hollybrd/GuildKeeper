# GYMGUILD Discord Shop  -  Shopify integration

## What is already installed in code

**Discord shop panel:** From Admin Hub → Bot Controls → Shopify Store → Post / Move Shop, choose a channel. Or run /admin_shop_post with the channel. The permanent Shop panel offers Browse Products, New Arrivals, On Sale, Verify Order and Shop Online. Products are read from the store's public Shopify feed, excluding products tagged **Sample Product**. Product cards include product images, prices, and safe links to official Shopify product pages. Checkout and payment are handled only on Shopify, never inside Discord. If the public catalog is unavailable, the Shop Online link still works.

**Verified purchase milestone:** Verify Order asks a member PRIVATELY for an exact Shopify order number and checkout email. GuildKeeper asks Shopify to confirm the exact order name, purchaser email, PAID status and no cancellation. It stores the order's Shopify ID and verification timestamp but does NOT store their email. A positive match creates a pending First Purchase milestone request (if no prior claim/pending order) and a moderator review card. A moderator must still approve before XP is awarded; knowing an order number and email alone is not guaranteed identity proof. Existing /milestone_purchase remains as a manual fallback. Verification requires a separately configured Shopify Admin API app. Apps created in Shopify’s Dev Dashboard use a Client ID and Client Secret; GuildKeeper automatically requests and renews the approximately 24-hour access token.

**Rank reward codes:** Master 10%, Legend 15%, Paragon 20%. In a private reward ticket, a Manage Server moderator can choose **Generate Shopify Code** and confirm. Only after that approval will GuildKeeper create a 7-day, one-use discount through Shopify, post it inside the private ticket, and mark the ticket fulfilled after delivery. The bot stores a reservation first and will not blindly retry an uncertain API outcome or create another code for the same ticket. Shopify Discounts must be checked by a human if an API request is interrupted. Reward requests and approvals remain in the existing GuildKeeper system. This uses an Admin API app with write_discounts.

**Store event announcements (opt-in):** Setting SHOPIFY_AUTO_ANNOUNCE=1 enables a 30-minute polling job for new products and detected sale/price reductions. The first poll establishes a baseline, so existing inventory is not announced as new. Afterwards it uses the existing announcement deduplication and never pings roles. The feature is OFF by default. It may miss promotions that do not change public product prices. For a carefully timed drop, use the manual Announcement Scheduler instead.

## One-time store setup: private Shopify Admin API credentials

Your Shopify connection in ChatGPT is **separate** from GuildKeeper's Oracle server. It does not share credentials with the Discord bot.

1. In your Shopify admin, create an app for GuildKeeper using Shopify's current merchant app installation/development process. Grant only the scopes required: **read_orders** (for paid order verification) and **write_discounts** (for approved reward codes). Storefront catalog browsing needs no private app scope. Shopify may require approval for protected customer data such as order email.
2. Record the store's actual **.myshopify.com** domain and the app's Client ID and Client Secret. Do not use the public gymguild.co.uk domain for the Admin API endpoint. Do not paste the token into Discord, GitHub code, or ChatGPT.
3. Add the following to the **GUILDKEEPER_ENV** GitHub Actions secret used to deploy to Oracle (existing settings retained):

    SHOPIFY_ADMIN_DOMAIN=your-actual-shop.myshopify.com
    SHOPIFY_CLIENT_ID=your-app-client-id
    SHOPIFY_CLIENT_SECRET=your-private-client-secret
    SHOPIFY_AUTO_ANNOUNCE=0

4. Trigger the existing manual **Deploy GuildKeeper** workflow. It backs up the SQLite database and environment as before. Deployment is not triggered automatically by a GitHub merge.
5. Run **Admin Hub → Health Check** to see whether the Shopify settings are configured. In **Bot Controls → Shopify Store**, select the destination channel and post the public shop panel.
6. Use a designated test member and test order/reference in a safe environment to inspect the verification flow. For actual discounts, use a low-risk, legitimate reward ticket and confirm before issuing. No discounts are generated during code deployment or tests.

## Security and limitations

- The public shop is a **catalog, not an in-Discord checkout**. No payment/card data is accepted by GuildKeeper.
- The bot reads only the email typed in a private modal and compares it to the Shopify order email; it does not persist that email. Discord transmits modal input under its normal service policies.
- Order verification currently requires **moderator approval** for XP, to reduce fraudulent crediting. Cancelled or unpaid orders cannot be verified. Shopify refund or chargeback status can change later; moderator discretion still matters.
- The one-use reward code is a bearer code with a 7-day expiry. Anyone with the code could use it first; keep the ticket private. Code recovery after an uncertain creation requires looking in Shopify Discounts; do not repeatedly retry.
- If Shopify API credentials are absent, **browse/open shop** can still function; **Verify Order** uses the existing manual-submission fallback, and **Generate Shopify Code** stays disabled with an explanation.
- Product images and live prices depend on the Shopify public storefront feed and may differ from final checkout prices, shipping costs, and available sizes.
- Store events are opted in only when SHOPIFY_AUTO_ANNOUNCE=1. Polling every 30 minutes is not suitable for second-accurate launches; schedule those using the existing Announcements screen.
- Never reuse the ChatGPT Shopify connector token or any personal account password for the Discord bot.

## Future ideas

Optional collection menus, wishlist/watchlist, sale preview approvals, private order-status viewing without exposing address or email, discount usage reporting, and signed Shopify webhooks (instead of polling) can be added after the first live verification.
## Shopify 2026 authentication note

New Dev Dashboard apps do **not** display a permanent Admin API token. Configure `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET` instead. GuildKeeper exchanges them for an access token through Shopify’s client credentials grant, caches it in memory and requests a fresh token when needed. `SHOPIFY_ADMIN_TOKEN` remains supported for pre-2026 legacy installed custom apps. Store your client secret only in GitHub Actions' existing `GUILDKEEPER_ENV` secret, never in committed code or Discord messages.
