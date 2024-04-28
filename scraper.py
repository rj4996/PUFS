import sys
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class DescriptionScraper():
    @staticmethod
    def scrape(course_keyword):
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

        try:
            #open the princeton course offerings webpage 
            driver.get("https://registrar.princeton.edu/course-offerings")
            wait = WebDriverWait(driver, 10)

            #search for keyword
            input_field = wait.until(EC.visibility_of_element_located((By.ID, "cs-keyword")))
            input_field.send_keys(course_keyword)
            search_button = wait.until(EC.element_to_be_clickable((By.ID, "classes-search-button")))
            search_button.click()
            try:

                first_result_link = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "td.class-info a")))
                # Get the text of the catalog number and compare with keyword
                catalog_number_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "td.class-info small")))
                catalog_number = catalog_number_element.text.strip()

                # Check if the catalog number matches the keyword
                if course_keyword.replace(" ", "").lower() not in catalog_number.replace(" ", "").lower():
                    return "", "", ""

                #continue onto description page of course 
                href_attribute = first_result_link.get_attribute('href')
                base_url = "https://registrar.princeton.edu/"
                full_url = href_attribute if href_attribute.startswith('http') else base_url + href_attribute.lstrip('/')
                driver.get(full_url)

                description_container = wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'description')))
                description_text = description_container.find_element(By.TAG_NAME, 'p').text

                try:
                    # Locate the sample reading list by its HTML structure
                    reading_list_container = driver.find_element(By.CLASS_NAME, 'sample-reading-list')
                    # Extract all list items within the reading list
                    list_items = reading_list_container.find_elements(By.TAG_NAME, 'li')
                    reading_list = [item.text for item in list_items]
                except Exception as e:
                    reading_list = "Not provided"

                try:
                    website_link = "Not provided"
                    other_info_elements = driver.find_elements(By.CLASS_NAME, 'other-information')
                    # Iterate through the elements to find the one containing the course website
                    for element in other_info_elements:
                        # Check if the element contains a link
                        links = element.find_elements(By.TAG_NAME, 'a')
                        for link in links:
                            if 'http' in link.get_attribute('href'):
                                website_link = link.get_attribute('href')
                except Exception as e:
                    website_link = "Not provided"
            except Exception as e:
                description_text, reading_list, website_link = "", "", ""
            return description_text, reading_list, website_link
        except Exception as e:
            print(f"An error occurred with {course_keyword}: {e}")
            return None
        finally:
            driver.quit()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python scraper.py <course_keyword>")
        sys.exit(1)
    
    keyword = sys.argv[1]
    description, reading_list, website_link = DescriptionScraper.scrape(keyword)
    if description and reading_list:
        print(json.dumps({"description": description, "sample_reading_list": reading_list, "website": website_link}))
    else:
        print(json.dumps({"error": "Could not find course"}))
