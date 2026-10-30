# Request to FAA and DOT: let the site's reader through

Draft for the owner to send from their own address (not sent by the newsroom). Every statement below is true of
the code as of 2026-10-30; check again before re-sending if the reader changes.

**To:** webmanagement@faa.gov, pressoffice@faa.gov
**Cc:** pressoffice@dot.gov
(DOT lists no web-team address; its press office is the published contact for news organizations.)

**Subject:** Access for a news site's reader to FAA and DOT press releases (HTTP 403)

Hello,

I publish Advanced Transportation Live (https://advancedtransportation.live), a news site covering drones, advanced
air mobility, autonomous vehicles and rail. It links readers to the original FAA and DOT press releases and
summarizes them.

The site's reader cannot open your press releases. Every request from it to www.faa.gov and
www.transportation.gov, including robots.txt, gets HTTP 403. It identifies itself with this User-Agent:

    AdvancedTransportationNewsBot/1.0 (+https://advancedtransportation.live)

faa.gov's robots.txt allows all crawlers into /newsroom, so I believe a firewall rule is catching the word "Bot"
rather than a decision to keep news readers out. I would rather ask than disguise the reader as a browser.

What the reader does:
- It reads each press release page once, after the release appears in a news search for faa.gov or
  transportation.gov, plus FAA or DOT pages that news articles link to. That is a handful of pages a day.
- It reads robots.txt once a day per site and follows it.
- It does not crawl the site or download files. At most it follows one link from a listing page to the release
  it was looking for.

Could you allow that User-Agent to read pages under https://www.faa.gov/newsroom/ and
https://www.transportation.gov/briefing-room/ and https://www.transportation.gov/newsroom/? If you publish an RSS feed or
email list for press releases that a program may read, I would gladly use that instead.

Thank you,
[Name]
Advanced Transportation Live, https://advancedtransportation.live
[Contact email]
