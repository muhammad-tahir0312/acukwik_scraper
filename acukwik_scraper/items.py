# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy

class AirportItem(scrapy.Item):
    airport = scrapy.Field()
    additional_airport_data = scrapy.Field()
    airport_info = scrapy.Field()
    contact_info = scrapy.Field()
    fbo_info = scrapy.Field()
    handler_info = scrapy.Field()
    caterers_info = scrapy.Field()
    hotel_info = scrapy.Field()
    car_rental_info = scrapy.Field()
