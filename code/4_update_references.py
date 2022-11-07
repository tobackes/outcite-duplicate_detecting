#-IMPORTS-----------------------------------------------------------------------------------------------------------------------------------------
import sys
import re
from elasticsearch import Elasticsearch as ES
from elasticsearch.helpers import streaming_bulk as bulk
from common import *
#-------------------------------------------------------------------------------------------------------------------------------------------------
#-GLOBAL OBJECTS----------------------------------------------------------------------------------------------------------------------------------

_index = sys.argv[1];

_recheck = True;
_ids     = [];

_chunk_size       =  250;
_requestimeout    =   60;
_scroll_size      =  500;
_max_extract_time =    1; #minutes
_max_scroll_tries =    2;

_featyp, _ngrams_n         = 'ngrams', 3; #words #wordgrams #None #5

_similarities, _thresholds = ['jaccard'], [[0.8]]; #jaccard #f1 #overlap #None
_XF_type,_FF_type,_FX_type = 'PROB', 'PROB_thr', 'PROB';

_refobjs = [    'anystyle_references_from_cermine_fulltext',
                'anystyle_references_from_cermine_refstrings',
                'anystyle_references_from_grobid_fulltext',
                'anystyle_references_from_grobid_refstrings',   #                'anystyle_references_from_gold_fulltext',
                'cermine_references_from_cermine_refstrings',          #                'anystyle_references_from_gold_refstrings',
                'cermine_references_from_grobid_refstrings',#,    #                'cermine_references_from_gold_refstrings',
                'grobid_references_from_grobid_xml',
                'exparser_references_from_cermine_layout'
                ];

_fields = ['reference','volume','issue','year','start','end','title','source','place','authors','editors','publishers'];

_scr_query = { "ids": { "values": _ids } } if _ids else {'bool':{'must_not':{'term':{'has_duplicates': True}}}} if not _recheck else {'match_all':{}};

_body = { '_op_type': 'update', '_index': _index, '_id': None, '_source': { 'doc': { 'has_duplicates': True } } };

#-------------------------------------------------------------------------------------------------------------------------------------------------
#-FUNCTIONS---------------------------------------------------------------------------------------------------------------------------------------

def update_refobjects(refobjects,fromID,toolchain,client,index,fields):
    query          = {'term':{'ids.keyword':None}};
    dupIDs         = [];
    new_refobjects = [];
    for i in range(len(refobjects)):
        refID = toolchain+'_'+fromID+'_ref_'+str(i);
        new_refobjects.append(copy(refobjects[i]));
        new_refobjects[-1]['id']     = refID;
        query['term']['ids.keyword'] = refID;
        results                      = [(result['_source'],result['_id'],) for result in client.search(index=index,query=query)['hits']['hits']];
        #print(len(results),'results in duplicates index for',refID);
        if len(results) > 0:
            duplicate, dupID = results[0];
            new_refobjects[-1]['duplicate_id'] = dupID;
            if refID in duplicate['ids']:
                dupIDs.append(dupID);
                for field in fields: # Remember that the entire reference will be REPLACED not updated!
                    new_refobjects[-1][field+'_original'] = new_refobjects[-1][field+'_original'] if field+'_original' in new_refobjects[-1] else new_refobjects[-1][field] if field in new_refobjects[-1] else None;
                    new_refobjects[-1][field]             = duplicate[field] if field in duplicate else None;
                #print(new_refobjects[-1]);
            else:
                print('Could not find',refID,'in',duplicate['ids'],'.');
    return new_refobjects,dupIDs;

def update_docs(index,index_m,fields):
    client   = ES(['localhost'],scheme='http',port=9200,timeout=60);
    client_m = ES(['localhost'],scheme='http',port=9200,timeout=60);
    page     = client.search(index=index,scroll=str(int(_max_extract_time*_scroll_size))+'m',size=_scroll_size,query=_scr_query);
    sid      = page['_scroll_id'];
    returned = len(page['hits']['hits']);
    page_num = 0;
    while returned > 0:
        for doc in page['hits']['hits']:
            body        = copy(_body);
            body['_id'] = doc['_id'];
            #------------------------------------------------------------------------------------------------------------------------------
            for refobj in _refobjs:
                previous_refobjects    = doc['_source'][refobj] if refobj in doc['_source'] and doc['_source'][refobj] else [];
                new_refobjects, dupIDs = update_refobjects(previous_refobjects,doc['_id'],refobj,client_m,index_m,fields);
                if len(dupIDs) == 0:
                    continue;
                body['_source']['doc'][refobj] = new_refobjects;
            #------------------------------------------------------------------------------------------------------------------------------
                #print(refobj,'=> Updating references of',body['_id'],'by attributes of duplicates',[refobject['id']+' --> '+str(refobject['duplicate_id']) for refobject in new_refobjects if 'duplicate_id' in refobject]);
            yield body;
        scroll_tries = 0;
        while scroll_tries < _max_scroll_tries:
            try:
                page      = client.scroll(scroll_id=sid, scroll=str(int(_max_extract_time*_scroll_size))+'m');
                returned  = len(page['hits']['hits']);
                page_num += 1;
            except Exception as e:
                print(e, file=sys.stderr);
                print('\n[!]-----> Some problem occured while scrolling. Sleeping for 3s and retrying...\n');
                returned      = 0;
                scroll_tries += 1;
                time.sleep(3); continue;
            break;
    client.clear_scroll(scroll_id=sid);

#-------------------------------------------------------------------------------------------------------------------------------------------------
#-SCRIPT------------------------------------------------------------------------------------------------------------------------------------------

_client = ES(['localhost'],scheme='http',port=9200,timeout=60);

i = 0;
for success, info in bulk(_client,update_docs(_index,'duplicates',_fields),chunk_size=_chunk_size, request_timeout=_requestimeout):
    i += 1;
    if not success:
        print('\n[!]-----> A document failed:', info['index']['_id'], info['index']['error'],'\n');
    #print(i,info)
    if i % _chunk_size == 0:
        print(i,'refreshing...');
        _client.indices.refresh(index=_index);
print(i,'refreshing...');
_client.indices.refresh(index=_index);
#-------------------------------------------------------------------------------------------------------------------------------------------------
