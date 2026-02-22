INSERT INTO datasource (name, implementation, config)
VALUES (
    'bpi-fund-rest-json',
    'price_sources.bpi_fund_datasource:BPIFundDataSource',
    '{
        "url": "https://www.bpi.com.ph/content/bpi/ph/en/group/bpiwealth/our-solutions/personal/investment-solutions/funds/short-term-invest-fund/jcr:content/root/container/dynamicgraph_copy.model.json",
        "method": "GET",
        "headers": {
            "Accept": "* / *",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en,en-US;q=0.9",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0"
        }
    }'::jsonb
  );
INSERT INTO datasource (name, implementation, config)
VALUES (
    'manulife-fund-rest-json',
    'price_sources.manulife_fund_datasource:ManulifeFundDataSource',
    '{
        "url": "https://www.manulifeim.com.ph/our-funds/fund-details/_jcr_content/root/responsivegrid_1172645951/responsivegrid/funddetails.prices.fid-{code}.html",
        "method": "GET",
        "headers": {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en,en-US;q=0.9",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0"
        }
    }'::jsonb
  );
INSERT INTO datasource (name, implementation, config)
VALUES (
    'slamc-fund-rest-json',
    'price_sources.slamc_fund_datasource:SLAMCFundDataSource',
    '{
        "url": "https://www.sunlife.com.ph/funds/navprice/mf",
        "method": "POST",
        "headers": {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en,en-US;q=0.9",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0"
        }
    }'::jsonb
  );

INSERT INTO entity (code, name, datasource_id, type, frequency, has_weekend, timezone, config)
VALUES (
    'U1STF',
    'BPI Short Term Fund',
    '0c725aae-3335-489f-8757-d78c32634c55',
    'FUND',
    'DAILY',
    true,
    'Asia/Manila',
    '{}' :: jsonb
);
INSERT INTO entity (code, name, datasource_id, type, frequency, has_weekend, timezone, config)
VALUES (
    'GT4',
    'Manulife Global Technology Equity Feeder Fund PhP-Unhedged Share Class A',
    '0240e2e7-620a-46ee-978c-89356d55f338',
    'FUND',
    'DAILY',
    false,
    'Asia/Manila',
    '{}' :: jsonb
);
INSERT INTO entity (code, name, datasource_id, type, frequency, has_weekend, timezone, config)
VALUES (
    'AG4',
    'Manulife American Growth Equity Feeder Fund PhP-Unhedged Share Class A',
    '0240e2e7-620a-46ee-978c-89356d55f338',
    'FUND',
    'DAILY',
    false,
    'Asia/Manila',
    '{}' :: jsonb
);
INSERT INTO entity (code, name, datasource_id, type, frequency, has_weekend, timezone, config)
VALUES (
    'CF0016',
    'Sun Life Prosperity World Equity Index Feeder Fund',
    '6b26a17e-aa97-46ce-918a-136680223354',
    'FUND',
    'DAILY',
    false,
    'Asia/Manila',
    '{}'
);
INSERT INTO entity (code, name, datasource_id, type, frequency, has_weekend, timezone, config)
VALUES (
    'CF0001',
    'Sun Life Prosperity Bond Fund',
    '6b26a17e-aa97-46ce-918a-136680223354',
    'FUND',
    'DAILY',
    false,
    'Asia/Manila',
    '{}'
);
INSERT INTO entity (code, name, datasource_id, type, frequency, has_weekend, timezone, config)
VALUES (
    'CF0002',
    'Sun Life Prosperity Balanced Fund',
    '6b26a17e-aa97-46ce-918a-136680223354',
    'FUND',
    'DAILY',
    false,
    'Asia/Manila',
    '{}'
);
INSERT INTO entity (code, name, datasource_id, type, frequency, has_weekend, timezone, config)
VALUES (
    'CF0003',
    'Sun Life Prosperity Philippine Equity Fund',
    '6b26a17e-aa97-46ce-918a-136680223354',
    'FUND',
    'DAILY',
    false,
    'Asia/Manila',
    '{}'
);
INSERT INTO entity (code, name, datasource_id, type, frequency, has_weekend, timezone, config)
VALUES (
    'CF0009',
    'Sun Life Prosperity Index Fund',
    '6b26a17e-aa97-46ce-918a-136680223354',
    'FUND',
    'DAILY',
    false,
    'Asia/Manila',
    '{}'
);