# Import all the required libraries and functionalities
import time
from pathlib import Path
from typing import List, Tuple

from langchain import PromptTemplate, LLMChain
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from langchain.vectorstores.faiss import FAISS
from langchain_community.document_loaders.parsers.pdf import PDFPlumberParser

from langchain_community.document_loaders import TextLoader, UnstructuredPDFLoader, PyPDFLoader, DirectoryLoader, UnstructuredHTMLLoader, BSHTMLLoader, DataFrameLoader, UnstructuredExcelLoader
from langchain_community.document_loaders.pdf import PDFPlumberLoader
from langchain_community.document_loaders.csv_loader import UnstructuredCSVLoader, CSVLoader
from langchain_community.document_loaders import MHTMLLoader
from langchain_community.document_loaders.web_base import WebBaseLoader
from langchain_community.document_loaders import AzureAIDocumentIntelligenceLoader

# from atlassian import Confluence
from langchain_community.document_loaders import ConfluenceLoader, UnstructuredXMLLoader
from langchain.storage import InMemoryStore
from langchain.retrievers import ParentDocumentRetriever, BM25Retriever
from langchain.docstore.document import Document

import pandas as pd
import pickle
import os
import shutil
import re

def load_from_pickle(filename: str) -> object:
    """
    Load an object from a pickle file.

    Parameters:
    filename (str): Path to the pickle file.

    Returns:
    object: The loaded object.
    """
    with open(filename, "rb") as file:
        return pickle.load(file)
    
def save_object(obj: object, filename: str) -> None:
    """
    Save an object to a pickle file.

    Parameters:
    obj (object): The object to be saved.
    filename (str): Path to the pickle file.
    """
    with open(filename, 'wb') as outp:
        pickle.dump(obj, outp, pickle.HIGHEST_PROTOCOL)

def initialize_embeddings() -> HuggingFaceEmbeddings:
    """
    Initialize and return HuggingFaceEmbeddings.

    Returns:
    HuggingFaceEmbeddings: The initialized embeddings object.
    """
    model_name = "./model/mixedbread-ai_mxbai-embed-large-v1/"
    model_kwargs = {'device': 'cpu'}
    encode_kwargs = {'normalize_embeddings': False}
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs=model_kwargs,
        encode_kwargs=encode_kwargs
    )
embeddings = initialize_embeddings()

def load_documents(dir: str) -> List:
    """
    Load documents from a specified directory and split them based on Markdown headers.

    Parameters:
    dir (str): Path to the directory containing the documents.

    Returns:
    List: List of split documents with metadata.
    """
    dirs = os.listdir(dir)
    docs = []
    headers_to_split_on = [("#", "Header 1"), ("##", "Header 2"), ("###", "Header 3")]
    md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on, strip_headers=False)
    for file in dirs:
        if file.endswith('.md'):
            with open(os.path.join(dir, file), 'r', encoding="utf-8") as file:
                data = file.read()
                html_header_splits = md_splitter.split_text(data)
                for doc in html_header_splits:
                    doc.metadata['source'] = Path(file.name).stem + '.md'
                    # doc.metadata['content'] = doc.page_content
                    docs.append(doc)
    return docs

def split_chunks(sources: List, child_splitter) -> List:
    """
    Split documents into chunks using the provided splitter.

    Parameters:
    sources (List): List of documents to be split.
    child_splitter: The splitter to use for chunking the documents.

    Returns:
    List: List of chunks.
    """
    chunks = []
    for chunk in child_splitter.split_documents(sources):
        chunks.append(chunk)
    return chunks

def generate_index(chunks: List, embeddings: HuggingFaceEmbeddings) -> FAISS:
    """
    Generate and return a FAISS index from the chunks.

    Parameters:
    chunks (List): List of document chunks.
    embeddings (HuggingFaceEmbeddings): Embeddings object for generating vector representations.

    Returns:
    FAISS: The generated FAISS index.
    """
    texts = [doc.page_content for doc in chunks]
    metadatas = [doc.metadata for doc in chunks]
    return FAISS.from_texts(texts, embeddings, metadatas=metadatas)

def combine_files(source_folders, destination_folder):
    """
    Combine files from multiple folders into a single folder.
    
    Parameters:
    source_folders (list): List of paths to the source folders.
    destination_folder (str): Path to the destination folder.
    """
    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)
    
    files = os.listdir(destination_folder)
    for file in files:
        file_path = os.path.join(destination_folder, file)
        if os.path.isfile(file_path):
            os.remove(file_path)
    
    for folder in source_folders:
        if os.path.exists(folder):
            for filename in os.listdir(folder):
                source_file = os.path.join(folder, filename)
                if os.path.isfile(source_file):
                    destination_file = os.path.join(destination_folder, filename)
                    if not os.path.exists(destination_file):  # Avoid overwriting
                        shutil.copy(source_file, destination_file)

def add_index(index_dir: str, updated_index_dir: str, save_dir: str, combined_dir: str, data_dir: str, updated_data_dir: str) -> FAISS:
    """
    Add a new index to an existing index, update the document retrievers, and combine files.

    Parameters:
    index_dir (str): Directory of the original index.
    updated_index_dir (str): Directory of the updated index.
    save_dir (str): Directory to save the merged index.
    combined_dir (str): Directory that has all the files indexed.
    data_dir (str): Directory containing the original documents.
    updated_data_dir (str): Directory containing the updated documents.

    Returns:
    FAISS: The updated FAISS index.
    """
    index1 = FAISS.load_local(index_dir + "/index_files/", embeddings, allow_dangerous_deserialization=True)
    index2 = FAISS.load_local(updated_index_dir + "/index_files/", embeddings, allow_dangerous_deserialization=True)
    index1.merge_from(index2)
    combine_files([data_dir, updated_data_dir], combined_dir)
    sources = load_documents(combined_dir)
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64, separators=[" ", ",", "\n"])  # chunk_overlap=16
    parent_splitter = RecursiveCharacterTextSplitter(chunk_size=2048, chunk_overlap=256)

    store = InMemoryStore()
    retriever = ParentDocumentRetriever(
        vectorstore=index1,
        docstore=store,
        child_splitter=child_splitter,
        parent_splitter=parent_splitter
    )
    bm25_retriever = BM25Retriever.from_documents(sources)
    bm25_retriever.k = 2

    retriever.add_documents(sources)
    index1.save_local(save_dir + "/index_files/")
    save_object(store, './' + save_dir + '/retriever.pkl')
    save_object(bm25_retriever, './' + save_dir + '/bm25.pkl')
    print("Addition is done perfectly.")

def load_faiss_index(index_dir: str) -> FAISS:
    """
    Load a FAISS index from a specified directory.

    Parameters:
    index_dir (str): Path to the directory containing the FAISS index.

    Returns:
    FAISS: The loaded FAISS index.
    """
    index = FAISS.load_local(index_dir + "/index_files/", embeddings, allow_dangerous_deserialization=True)
    return index

def store_to_df(index: FAISS) -> pd.DataFrame:
    """
    Convert the document store in the FAISS index to a pandas DataFrame.

    Parameters:
    index (FAISS): The FAISS index object.

    Returns:
    pd.DataFrame: DataFrame representation of the document store.
    """
    v_dict = index.docstore._dict
    data_rows = []
    for i in v_dict.keys():
        doc_name = v_dict[i].metadata['source']
        heading_1 = v_dict[i].metadata.get('Header 1', 'N/A')
        heading_2 = v_dict[i].metadata.get('Header 2', 'N/A') # In few cases, the heading_2 may not be present.
        content = v_dict[i].page_content
        data_rows.append({"chunk_id": i, "document": doc_name, "heading 1": heading_1, "heading 2": heading_2, "content": content})
    vector_df = pd.DataFrame(data_rows)
    return vector_df

def del_document_from_index(index_dir: str, document: List[str], save_dir: str, data_dir: str) -> None:
    """
    Delete specified documents from a FAISS index, update the retrievers, and save the modified index.

    Parameters:
    index_dir (str): Directory of the FAISS index.
    document (List[str]): List of document names to be deleted.
    save_dir (str): Directory to save the modified index.
    data_dir (str): Directory containing the documents.

    Returns:
    None
    """
    # Load FAISS index
    store = FAISS.load_local(index_dir + "/index_files/", embeddings, allow_dangerous_deserialization=True)
    
    # Remove documents from the filesystem
    for doc_name in document:
        file_path = os.path.join(data_dir, doc_name)
        if os.path.exists(file_path):
            os.remove(file_path)
    
    # Convert the store to a DataFrame to identify chunks to be deleted
    index = FAISS.load_local(index_dir + "/index_files/", embeddings, allow_dangerous_deserialization=True)
    vector_df = store_to_df(index)
    chunks_list = vector_df.loc[vector_df['document'].isin(document)]['chunk_id'].tolist()
    
    # Delete the identified chunks from the store
    store.delete(chunks_list)
    
    # Reload the remaining documents and update the retrievers
    sources = load_documents(data_dir)
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64, separators=[" ", ",", "\n"])
    parent_splitter = RecursiveCharacterTextSplitter(chunk_size=2048, chunk_overlap=256)

    store_1 = InMemoryStore()
    retriever = ParentDocumentRetriever(
        vectorstore=store,
        docstore=store_1,
        child_splitter=child_splitter,
        parent_splitter=parent_splitter
    )
    bm25_retriever = BM25Retriever.from_documents(sources)
    bm25_retriever.k = 2

    retriever.add_documents(sources)
    
    # Ensure save directory exists
    os.makedirs(save_dir + "/index_files", exist_ok=True)
    
    # Save the updated FAISS index
    store.save_local(os.path.join(save_dir, "index_files/"))
    
    # Save the retriever and bm25_retriever objects
    with open(os.path.join(save_dir, 'retriever.pkl'), 'wb') as f:
        pickle.dump(store_1, f)
        
    with open(os.path.join(save_dir, 'bm25.pkl'), 'wb') as f:
        pickle.dump(bm25_retriever, f)

    print("Deletion is done perfectly.")

def select_document_chunks(df: pd.DataFrame, document_name: str) -> pd.DataFrame:
    """
    Select chunks from the DataFrame for a specific document.

    Parameters:
    df (pd.DataFrame): DataFrame containing document information.
    document_name (str): The name of the document to select chunks for.

    Returns:
    pd.DataFrame: DataFrame containing chunks for the specified document.
    """
    return df.loc[df['document'].isin(document_name)]

def del_document_to_update(store: FAISS, vector_df: pd.DataFrame, document: List[str], data_dir: str) -> None:
    """
    Delete specified documents and their chunks from a FAISS index and the filesystem.

    Parameters:
    store (FAISS): FAISS index object.
    vector_df (pd.DataFrame): DataFrame mapping document names to chunk IDs.
    document (List[str]): List of document names to be deleted.
    data_dir (str): Directory containing the documents.

    Returns:
    None
    """
    for doc_name in document:
        file_path = os.path.join(data_dir, doc_name)
        if os.path.exists(file_path):
            os.remove(file_path)
        
    # Convert the store to a DataFrame to identify chunks to be deleted
    chunks_list = vector_df.loc[vector_df['document'].isin(document)]['chunk_id'].tolist()
    
    # Delete the identified chunks from the store
    store.delete(chunks_list)

def update_text(df: pd.DataFrame, old_text: str, new_text: str, df_selected: pd.DataFrame) -> pd.DataFrame:
    """
    Update text in the specified DataFrame.

    Parameters:
    df (pd.DataFrame): DataFrame containing document chunks.
    old_text (str): The text to be replaced.
    new_text (str): The text to replace with.
    df_selected (pd.DataFrame): DataFrame containing the selected document chunks to be updated.

    Returns:
    pd.DataFrame: Updated DataFrame with replaced text.
    """
    pattern = re.compile(re.escape(old_text), re.IGNORECASE)
    df_selected['content'] = df_selected['content'].apply(lambda text: pattern.sub(new_text, text))
    return df_selected

def update_faiss_index(index: FAISS, df: pd.DataFrame, save_dir: str, data_dir: str) -> None:
    """
    Update the FAISS index with new content and save the updated index.

    Parameters:
    index (FAISS): FAISS index object to be updated.
    df (pd.DataFrame): DataFrame containing updated document content.
    save_dir (str): Directory to save the updated FAISS index.
    data_dir (str): Directory containing the documents.

    Returns:
    None
    """
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64, separators=[" ", ",", "\n"])
    parent_splitter = RecursiveCharacterTextSplitter(chunk_size=2048, chunk_overlap=256)
    sources = load_documents(data_dir)
    store = InMemoryStore()
    retriever = ParentDocumentRetriever(
        vectorstore=index,
        docstore=store,
        child_splitter=child_splitter,
        parent_splitter=parent_splitter
    )
    if sources:
        retriever.add_documents(sources)
        bm25_retriever = BM25Retriever.from_documents(sources)
    
    # Update the document store with new content
    updated_documents = []
    for i, row in df.iterrows():
        doc_id = row['chunk_id']
        new_content = row['content']
        heading_1 = row["heading 1"]
        heading_2 = row["heading 2"]
        document = Document(page_content=new_content, metadata={'id': doc_id, "heading 1": heading_1, "heading 2": heading_2})
        updated_documents.append(document)
    retriever.add_documents(updated_documents)
    bm25_retriever = BM25Retriever.from_documents(updated_documents)
    bm25_retriever.k = 2

    # Save the updated FAISS index
    index.save_local(os.path.join(save_dir, "index_files/"))
    save_object(store, './' + save_dir + '/retriever.pkl')
    save_object(bm25_retriever, './' + save_dir + '/bm25.pkl')

def update_document_being_specified(index_dir: str, save_dir: str, document_name: str, old_text: str, new_text: str, data_dir: str) -> None:
    """
    Update specified documents in the FAISS index by replacing old text with new text.

    Parameters:
    index_dir (str): Directory containing the FAISS index to be updated.
    save_dir (str): Directory to save the updated FAISS index.
    document_name (str): Name of the document to be updated.
    old_text (str): The text to be replaced.
    new_text (str): The text to replace with.
    data_dir (str): Directory containing the documents.

    Returns:
    None
    """
    index = load_faiss_index(index_dir)
    df = store_to_df(index)
    df_selected = select_document_chunks(df, document_name)
    del_document_to_update(index, df, document_name, data_dir)
    df_updated = update_text(df_selected, old_text, new_text, df_selected)
    update_faiss_index(index, df_updated, save_dir, data_dir)
    print("Index is generated")

import sys
# Main workflow: Based on user input, perform Add, Delete, or Update operations
inp = sys.argv[1]
if inp == 'Add':
    index_dir = "./base_index_dir/" # base folder with the index and retriever files
    data_dir = "./base_data_dir" # initial markdown folder before addition
    updated_data_dir = "./data_dir_to_be_appended" # the index folder that should be added to the base folder
    updated_index_dir = "./index_dir_to_be_appended/" # the markdown folder to be added
    output_dir = "./save_dir" # Combined Index is Generated in it.
    combined_dir = './combined_data_dir' # All the markdown files that are index, are present over here.
    if os.path.exists(output_dir):
        os.mkdir(output_dir)
    if os.path.exists(combined_dir):
        os.mkdir(combined_dir)
    add_index(index_dir, updated_index_dir, output_dir, combined_dir, data_dir, updated_data_dir)

elif inp == 'Delete':
    # Specify the document names to be deleted in the following format 'file_name.md'.
    doc_name = sys.argv[2] # provide the document names with '.md' extension, in the form of list by seperating with ','.
    initial_index_dir = "./base_index_dir/" # index folder before deletion.
    save_dir = "./save_dir" # newly generated index is stored over here.
    data_dir = './base_data_dir' # Data directory of the markdown files that are in the given index files.
    if os.path.exists(save_dir):
        os.mkdir(save_dir)
    del_document_from_index(initial_index_dir, doc_name, save_dir,data_dir)

elif inp == 'Update':
    index_dir = "./input_index_dir" # index folder before updation
    save_dir = "./save_dir/" # index folder after updation
    # Specify the file names to be updated.
    document_name = sys.argv[2]  # provide the document names with '.md' extension, in the form of list by seperating with ','.
    old_text = sys.argv[3] # Text to be updated
    new_text = sys.argv[4] # Text to be retained after the updation                      
    data_dir = "./input_data_dir/" # folder with the markdown files of the index provided to update
    update_document_being_specified(index_dir, save_dir, document_name, old_text, new_text, data_dir)


# To check whether the Index is Merged/Deleted/Updated
query = 'Enter the Query' 
print("QUERY - " + query)

new_db = FAISS.load_local(dir + '/index_files', embeddings, allow_dangerous_deserialization=True)

docs = new_db.similarity_search(query)
docs