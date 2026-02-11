# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
from itemadapter import ItemAdapter
from pymongo import MongoClient
import scrapy
from scrapy.exceptions import DropItem
import string 
from scrapy.pipelines.images import ImagesPipeline
import os

file_name = None

class AcukwikScraperPipeline:
    
    def process_item(self, item, spider):
        return item

class ImageDownloadingPipeline(ImagesPipeline):

    def get_media_requests(self, item, info):

        print("Outside")
        if item.get("Runway Diagram Url") != None: 
            global file_name 
            file_name = item.get("Runway Diagram Image Name")
            
            print(f"Downloading image from url: {item['Runway Diagram Url']}")
            referer = item.get("Referer Url")
            headers = {'Referer': referer} if referer else {}
            return scrapy.Request(
                item["Runway Diagram Url"],
                headers=headers,
                meta={'item': item}
            )
        print(f"No Image URL Found {item}")
        return None 

    def file_path(self, request, response=None, info=None, item=None):

        filename = f"{file_name}"
        return f'downloaded_images/{filename}'


    def item_completed(self, results, item, info):
        print(f"Image pipeline results: {results}")
        if results and item.get("Runway Diagram Local Path"):
            for success, value in results:
                if success:
                    item["runway_diagram_path"] = value["path"]
                    print(f"Saved image at: {item['runway_diagram_path']}")
                    return item
        print("Image download failed.")
        return item



class DataCleaningAndStoringPipeline:
    
    def __init__(self, nosql_uri):
        self.nosql_uri = nosql_uri
        

        self.nosql_client = MongoClient(nosql_uri)
        self.nosql_db = self.nosql_client['airport_db']
        self.nosql_collection = self.nosql_db['airport_info']

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            nosql_uri=crawler.settings.get('NOSQL_URI')
        )

    def process_item(self, item, spider):
        cleaned_item = self.clean_data(item)


        self.store_in_nosql(cleaned_item)

        return cleaned_item
    
    def store_in_nosql(self, item):
        self.nosql_collection.insert_one(dict(item))

    

    def clean_data(self, item):

        exclude_keys = {"Contact Information"}
        for key, value in item.items():
            if key not in exclude_keys and isinstance(value, list):  
                item[key] = [
                    {
                        **entry,  # Keep all existing fields
                        "Email": entry.get("Email").replace("mailto:", "").strip()
                            if isinstance(entry.get("Email"), str) and entry.get("Email").startswith("mailto:")
                            else entry.get("Email")
                    }
                    for entry in value
                    if isinstance(entry, dict) and
                    (entry.get('Name') not in ['N/A', None] or 
                     entry.get('CompanyName') not in ['N/A', None])
                ]
           
        return item

