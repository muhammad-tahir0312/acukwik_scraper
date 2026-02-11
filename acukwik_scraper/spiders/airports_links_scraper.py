import scrapy
from urllib.parse import urlparse
import csv
import os

class AirportsSpider(scrapy.Spider):
    name = "airports_links"
    allowed_domains = ["acukwik.com"]
    start_urls = ["https://acukwik.com/Power-Search-Airports"]

    def parse(self, response):
        by_country_div = response.css('div.byCountry')

        for country in by_country_div.css('ul li a'):
            country_name = country.css('::text').get().strip()
            country_link = response.urljoin(country.attrib['href'])
            yield scrapy.Request(url=country_link, callback=self.parse_item, meta={'country': country_name})

    def parse_item(self, response):
        country = response.meta['country']
        filename = f"{country}_airport_links.csv"

        if os.path.exists(filename):
            os.remove(filename)

        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['ICAO', 'Airport Name', 'City', 'State', 'Country', 'Airport Link']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for airport in response.css('div.result.clearfix'):
                airport_link = response.urljoin(airport.css('div.col2.w30p.fl.p10px a::attr(href)').get())
                item = {
                    'ICAO': airport.css('div.col1.w15p.fl.p10px::text').get(),
                    'Airport Name': airport.css('div.col2.w30p.fl.p10px a::text').get(),
                    'City': airport.css('div.col3.w15p.fl.p10px::text').get(),
                    'State': airport.css('div.col3.w15p.fl.p10px::text').getall()[1] if len(airport.css('div.col3.w15p.fl.p10px::text').getall()) > 1 else None,
                    'Country': airport.css('div.col4.w25p.fl.p10px::text').get(),
                    'Airport Link': airport_link,
                }
                writer.writerow(item)  
                yield item 
