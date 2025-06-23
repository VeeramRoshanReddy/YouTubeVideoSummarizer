import json
import os
import boto3
import urllib.request
import subprocess
import uuid
import re
import time
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from urllib.parse import urlencode

# AWS Clients
s3 = boto3.client('s3')
transcribe = boto3.client('transcribe')
comprehend = boto3.client('comprehend')

# S3 Buckets
CAPTIONS_BUCKET = 'video-captions-bucket-youtube-video-summarizer'
TRANSCRIPTIONS_BUCKET = 'video-transcriptions-bucket-youtube-video-summarizer'
SUMMARIES_BUCKET = 'video-summaries-bucket-youtube-video-summarizer'

# YouTube API Key
YOUTUBE_API_KEY = os.environ['YOUTUBE_DATA_API_KEY']

# OAuth Configuration
CLIENT_ID = os.environ['CLIENT_ID']
CLIENT_SECRET = os.environ['CLIENT_SECRET']
REDIRECT_URI = 'https://www.vidsummarize.online'

# Define CORS headers
def get_cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Requested-With",
        "Access-Control-Max-Age": "3600",
        "Access-Control-Allow-Credentials": "true",
        "Content-Type": "application/json"
    }

# Get YouTube video information including title
def get_video_info(video_id):
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        response = youtube.videos().list(
            part='snippet',
            id=video_id
        ).execute()
        
        if response['items']:
            return {
                'title': response['items'][0]['snippet']['title'],
                'description': response['items'][0]['snippet']['description']
            }
        return None
    except HttpError as e:
        print(f"Error fetching video info: {str(e)}")
        return None

# Get captions using YouTube API
def get_captions_via_api(video_id):
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        
        # Get caption tracks
        captions_response = youtube.captions().list(
            part='snippet',
            videoId=video_id
        ).execute()
        
        # Look for English captions
        caption_id = None
        for item in captions_response.get('items', []):
            if item['snippet']['language'] == 'en':
                caption_id = item['id']
                break
                
        if caption_id:
            # Download caption track
            caption_response = youtube.captions().download(
                id=caption_id,
                tfmt='srt'
            ).execute()
            
            # Process caption text (remove timestamps and formatting)
            text = re.sub(r'\d+\s*\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\s*\n', '', caption_response)
            text = re.sub(r'<[^>]+>', '', text)
            text = re.sub(r'\n+', ' ', text).strip()
            
            return text
    except HttpError as e:
        print(f"Error fetching captions via API: {str(e)}")
    return None

# Extract captions using yt-dlp
def get_youtube_captions(video_id):
    try:
        temp_dir = '/tmp'
        caption_file = f"{temp_dir}/{video_id}.en.vtt"

        ytdlp_cmd = [
            '/opt/bin/yt-dlp1',  # Make sure this path is correct for your Lambda layer
            f'https://www.youtube.com/watch?v={video_id}',
            '--write-auto-sub',
            '--sub-lang', 'en',
            '--sub-format', 'vtt',
            '--skip-download',
            '--no-cache-dir',
            '--user-agent', 'Mozilla/5.0',
            '-o', f'{temp_dir}/{video_id}.%(ext)s'
        ]

        subprocess.run(ytdlp_cmd, check=True)

        if os.path.exists(caption_file):
            with open(caption_file, 'r', encoding='utf-8') as f:
                content = f.read()

            text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3} --> .*?\n', '', content)
            text = re.sub(r'<[^>]+>', '', text)
            text = re.sub(r'\n+', ' ', text).strip()
            
            # Store captions in S3
            s3.put_object(
                Bucket=CAPTIONS_BUCKET,
                Key=f"{video_id}/captions.txt",
                Body=text,
                ContentType='text/plain',
                ServerSideEncryption="AES256"
            )
            
            return text
    except Exception as e:
        print(f"Error extracting captions using yt-dlp: {str(e)}")
    return None

# Extract audio using yt-dlp
def extract_audio(video_id):
    try:
        temp_dir = '/tmp'
        audio_file = f"{temp_dir}/{video_id}.mp3"

        ytdlp_cmd = [
            '/opt/bin/yt-dlp2',  # Make sure this path is correct for your Lambda layer
            f'https://www.youtube.com/watch?v={video_id}',
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            '--no-playlist',
            '--no-cache-dir',
            '-o', audio_file
        ]

        subprocess.run(ytdlp_cmd, check=True)
        return audio_file if os.path.exists(audio_file) else None
    except Exception as e:
        print(f"Error extracting audio: {str(e)}")
    return None

# Transcribe audio using AWS Transcribe
def transcribe_audio(audio_file, video_id):
    try:
        audio_s3_key = f"{video_id}/audio.mp3"
        s3.upload_file(audio_file, TRANSCRIPTIONS_BUCKET, audio_s3_key)

        job_name = f"transcribe-{video_id}-{uuid.uuid4().hex[:8]}"
        transcribe.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': f"s3://{TRANSCRIPTIONS_BUCKET}/{audio_s3_key}"},
            MediaFormat='mp3',
            LanguageCode='en-US'
        )

        # For Lambda, we should return the job name instead of waiting
        # The status can be checked with a separate API call
        return job_name
        
    except Exception as e:
        print(f"Error transcribing audio: {str(e)}")
    return None

# Check transcription job status
def check_transcription_status(job_name):
    try:
        status = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        job_status = status['TranscriptionJob']['TranscriptionJobStatus']
        
        if job_status == 'COMPLETED':
            transcript_uri = status['TranscriptionJob']['Transcript']['TranscriptFileUri']
            with urllib.request.urlopen(transcript_uri) as response:
                transcript_data = json.loads(response.read())
                transcript_text = transcript_data['results']['transcripts'][0]['transcript']

            return {
                'status': 'completed',
                'transcript': transcript_text
            }
        elif job_status == 'FAILED':
            return {
                'status': 'failed',
                'error': 'Transcription job failed'
            }
        else:
            return {
                'status': 'in_progress'
            }
    except Exception as e:
        print(f"Error checking transcription status: {str(e)}")
        return {
            'status': 'error',
            'error': str(e)
        }

# Summarize text using AWS Comprehend
def summarize_text(text, video_id, video_title):
    try:
        # Break down text into chunks to handle the 5000 character limit
        chunks = [text[i:i+5000] for i in range(0, len(text), 5000)]
        all_key_phrases = []
        
        for chunk in chunks[:3]:  # Process up to 3 chunks (15000 chars)
            response = comprehend.detect_key_phrases(
                Text=chunk, 
                LanguageCode='en'
            )
            all_key_phrases.extend([item['Text'] for item in response['KeyPhrases']])
        
        # Get sentiment
        sentiment_response = comprehend.detect_sentiment(
            Text=text[:5000],
            LanguageCode='en'
        )
        sentiment = sentiment_response['Sentiment']
        
        # Create a better formatted summary
        key_points = list(set(all_key_phrases))[:15]  # Remove duplicates and limit to top 15
        
        summary = f"# Summary of: {video_title}\n\n"
        summary += f"## Overall Sentiment: {sentiment.capitalize()}\n\n"
        summary += "## Key Points:\n\n"
        
        for i, point in enumerate(key_points, 1):
            summary += f"{i}. {point}\n"
        
        # Store summary in S3
        s3.put_object(
            Bucket=SUMMARIES_BUCKET,
            Key=f"{video_id}/summary.txt",
            Body=summary,
            ContentType='text/plain',
            ServerSideEncryption="AES256"
        )
        
        # Also store metadata
        metadata = {
            'videoId': video_id,
            'videoTitle': video_title,
            'timestamp': time.time(),
            'sentiment': sentiment
        }
        
        s3.put_object(
            Bucket=SUMMARIES_BUCKET,
            Key=f"{video_id}/metadata.json",
            Body=json.dumps(metadata),
            ContentType='application/json',
            ServerSideEncryption="AES256"
        )
        
        return summary
    except Exception as e:
        print(f"Error summarizing text: {str(e)}")
    return None

# Handle OAuth token exchange
def exchange_code_for_token(code, redirect_uri):
    try:
        token_url = "https://oauth2.googleapis.com/token"
        payload = {
            'code': code,
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code'
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        response = requests.post(
            token_url, 
            data=urlencode(payload),  # Using urlencode is important for OAuth
            headers=headers
        )
        
        print(f"Token exchange response status: {response.status_code}")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Token exchange failed: {response.text}")
            return None
    except Exception as e:
        print(f"Exception during token exchange: {str(e)}")
        return None

# Extract YouTube video ID from various URL formats
def extract_video_id(url):
    if not url:
        return None
    
    try:
        # Handle youtu.be short links
        if 'youtu.be/' in url:
            parts = url.split('youtu.be/')
            if len(parts) > 1:
                return parts[1].split('?')[0].split('&')[0]
        
        # Handle regular youtube.com links
        if 'youtube.com/watch' in url:
            parsed_url = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            if 'v' in query_params:
                return query_params['v'][0]
    except Exception as e:
        print(f"Error extracting video ID: {str(e)}")
    
    return None

# Lambda Handler
def lambda_handler(event, context):
    # Get CORS headers
    headers = get_cors_headers()
    
    # Print the incoming event for debugging
    print(f"Received event: {json.dumps(event)}")
    
    # Handle preflight OPTIONS requests properly
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'message': 'CORS preflight successful'})
        }
    
    # Extract path and HTTP method
    path = event.get('path', '')
    http_method = event.get('httpMethod', '')
    
    print(f"Processing path: {path}, method: {http_method}")
    
    # Handle OAuth endpoint
    if path == '/auth' and http_method == 'POST':
        try:
            body = json.loads(event.get('body', '{}'))
            code = body.get('code')
            redirect_uri = body.get('redirect_uri', REDIRECT_URI)
            
            if not code:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'message': 'Missing authorization code'})
                }
            
            # Exchange code for token
            token_data = exchange_code_for_token(code, redirect_uri)
            
            if token_data:
                return {
                    'statusCode': 200,
                    'headers': headers,
                    'body': json.dumps(token_data)
                }
            else:
                return {
                    'statusCode': 401,
                    'headers': headers,
                    'body': json.dumps({'message': 'Failed to exchange authorization code'})
                }
        except Exception as e:
            print(f"Error in auth endpoint: {str(e)}")
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'message': f'Error in auth endpoint: {str(e)}'})
            }
    
    # Process /summary/{videoId} endpoint
    elif path.startswith('/summary/') and http_method == 'POST':
        try:
            # Extract video ID from path parameters
            path_parameters = event.get('pathParameters', {}) or {}
            video_id = path_parameters.get('videoId')
            
            if not video_id:
                # Try to extract from the path directly as fallback
                parts = path.split('/summary/')
                if len(parts) > 1:
                    video_id = parts[1]
                    
                # If still no video_id, try to get from body as final fallback
                if not video_id:
                    try:
                        body = json.loads(event.get('body', '{}'))
                        video_id = body.get('videoId')
                    except:
                        video_id = None
            
            if not video_id:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'message': 'Missing videoId parameter'})
                }
            
            print(f"Processing video with ID: {video_id}")
            
            # Verify authorization (now optional for testing)
            auth_header = event.get('headers', {}).get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                print("Warning: Missing or invalid authorization. Continuing for testing.")
            
            # Check if summary already exists
            try:
                s3.head_object(Bucket=SUMMARIES_BUCKET, Key=f"{video_id}/summary.txt")
                # If no exception, summary exists - retrieve it
                response = s3.get_object(Bucket=SUMMARIES_BUCKET, Key=f"{video_id}/summary.txt")
                metadata_response = s3.get_object(Bucket=SUMMARIES_BUCKET, Key=f"{video_id}/metadata.json")
                metadata = json.loads(metadata_response['Body'].read().decode('utf-8'))
                
                return {
                    'statusCode': 200,
                    'headers': headers,
                    'body': json.dumps({
                        'status': 'completed',
                        'videoId': video_id,
                        'videoTitle': metadata.get('videoTitle', 'YouTube Video'),
                        'summary': response['Body'].read().decode('utf-8')
                    })
                }
            except Exception as s3_error:
                # Summary doesn't exist or other error, proceed with processing
                print(f"S3 error when checking existing summary: {str(s3_error)}")
            
            # Get video information
            video_info = get_video_info(video_id)
            if not video_info:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'message': 'Could not retrieve video information'})
                }
            
            video_title = video_info['title']
            
            # Try to get captions via API first
            caption_text = get_captions_via_api(video_id)
            
            if caption_text:
                # Captions available, generate summary
                summary = summarize_text(caption_text, video_id, video_title)
                
                if summary:
                    return {
                        'statusCode': 200,
                        'headers': headers,
                        'body': json.dumps({
                            'status': 'completed',
                            'videoId': video_id,
                            'videoTitle': video_title,
                            'summary': summary
                        })
                    }
            else:
                # No captions via API, try yt-dlp
                caption_text = get_youtube_captions(video_id)
                
                if caption_text:
                    # Captions available via yt-dlp, generate summary
                    summary = summarize_text(caption_text, video_id, video_title)
                    
                    if summary:
                        return {
                            'statusCode': 200,
                            'headers': headers,
                            'body': json.dumps({
                                'status': 'completed',
                                'videoId': video_id,
                                'videoTitle': video_title,
                                'summary': summary
                            })
                        }
                else:
                    # No captions at all, need to extract audio and transcribe
                    audio_file = extract_audio(video_id)
                    
                    if audio_file:
                        # Start transcription job
                        job_name = transcribe_audio(audio_file, video_id)
                        
                        if job_name:
                            # Store job info
                            job_info = {
                                'videoId': video_id,
                                'videoTitle': video_title,
                                'jobName': job_name,
                                'timestamp': time.time()
                            }
                            
                            s3.put_object(
                                Bucket=TRANSCRIPTIONS_BUCKET,
                                Key=f"{video_id}/job_info.json",
                                Body=json.dumps(job_info),
                                ContentType='application/json',
                                ServerSideEncryption="AES256"
                            )
                            
                            return {
                                'statusCode': 202,
                                'headers': headers,
                                'body': json.dumps({
                                    'status': 'processing',
                                    'videoId': video_id,
                                    'videoTitle': video_title,
                                    'jobId': job_name
                                })
                            }
            
            # If we reach here, all methods failed
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'message': 'Failed to process video'})
            }
        except Exception as e:
            print(f"Error processing video summary request: {str(e)}")
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'message': f'Internal server error: {str(e)}'})
            }
    
    # Also support /summarize endpoint for compatibility
    elif path == '/summarize' and http_method == 'POST':
        try:
            # Parse request body
            body = json.loads(event.get('body', '{}'))
            video_id = body.get('videoId')
            
            if not video_id:
                url = body.get('url', '')
                video_id = extract_video_id(url)
            
            if not video_id:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'message': 'Invalid or missing YouTube video ID'})
                }
            
            # Verify authorization (now optional for testing)
            auth_header = event.get('headers', {}).get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                print("Warning: Missing or invalid authorization. Continuing for testing.")
            
            # Continue with the same logic as /summary/{videoId}
            # Check if summary already exists
            try:
                s3.head_object(Bucket=SUMMARIES_BUCKET, Key=f"{video_id}/summary.txt")
                # If no exception, summary exists - retrieve it
                response = s3.get_object(Bucket=SUMMARIES_BUCKET, Key=f"{video_id}/summary.txt")
                metadata_response = s3.get_object(Bucket=SUMMARIES_BUCKET, Key=f"{video_id}/metadata.json")
                metadata = json.loads(metadata_response['Body'].read().decode('utf-8'))
                
                return {
                    'statusCode': 200,
                    'headers': headers,
                    'body': json.dumps({
                        'status': 'completed',
                        'videoId': video_id,
                        'videoTitle': metadata.get('videoTitle', 'YouTube Video'),
                        'summary': response['Body'].read().decode('utf-8')
                    })
                }
            except Exception as s3_error:
                # Summary doesn't exist or other error, proceed with processing
                print(f"S3 error when checking existing summary: {str(s3_error)}")
            
            # Get video information
            video_info = get_video_info(video_id)
            if not video_info:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'message': 'Could not retrieve video information'})
                }
            
            video_title = video_info['title']
            
            # Try to get captions via API first
            caption_text = get_captions_via_api(video_id)
            
            if caption_text:
                # Captions available, generate summary
                summary = summarize_text(caption_text, video_id, video_title)
                
                if summary:
                    return {
                        'statusCode': 200,
                        'headers': headers,
                        'body': json.dumps({
                            'status': 'completed',
                            'videoId': video_id,
                            'videoTitle': video_title,
                            'summary': summary
                        })
                    }
            else:
                # No captions via API, try yt-dlp
                caption_text = get_youtube_captions(video_id)
                
                if caption_text:
                    # Captions available via yt-dlp, generate summary
                    summary = summarize_text(caption_text, video_id, video_title)
                    
                    if summary:
                        return {
                            'statusCode': 200,
                            'headers': headers,
                            'body': json.dumps({
                                'status': 'completed',
                                'videoId': video_id,
                                'videoTitle': video_title,
                                'summary': summary
                            })
                        }
                else:
                    # No captions at all, need to extract audio and transcribe
                    audio_file = extract_audio(video_id)
                    
                    if audio_file:
                        # Start transcription job
                        job_name = transcribe_audio(audio_file, video_id)
                        
                        if job_name:
                            # Store job info
                            job_info = {
                                'videoId': video_id,
                                'videoTitle': video_title,
                                'jobName': job_name,
                                'timestamp': time.time()
                            }
                            
                            s3.put_object(
                                Bucket=TRANSCRIPTIONS_BUCKET,
                                Key=f"{video_id}/job_info.json",
                                Body=json.dumps(job_info),
                                ContentType='application/json',
                                ServerSideEncryption="AES256"
                            )
                            
                            return {
                                'statusCode': 202,
                                'headers': headers,
                                'body': json.dumps({
                                    'status': 'processing',
                                    'videoId': video_id,
                                    'videoTitle': video_title,
                                    'jobId': job_name
                                })
                            }
            
            # If we reach here, all methods failed
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'message': 'Failed to process video'})
            }
        except Exception as e:
            print(f"Error processing video summary request: {str(e)}")
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({'message': f'Internal server error: {str(e)}'})
            }
    
    # Handle status check endpoint
    elif path == '/status' and http_method == 'GET':
        # Get job ID from query parameters
        query_params = event.get('queryStringParameters', {}) or {}
        job_id = query_params.get('jobId')
        
        if not job_id:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'message': 'Missing job ID'})
            }
        
        # Check job status
        status = check_transcription_status(job_id)
        
        if status['status'] == 'completed':
            # Get video ID and title from job info
            video_id = job_id.split('-')[1] if '-' in job_id else None
            
            if not video_id:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'message': 'Invalid job ID format'})
                }
            
            try:
                # Get job info to retrieve video title
                job_info_response = s3.get_object(
                    Bucket=TRANSCRIPTIONS_BUCKET,
                    Key=f"{video_id}/job_info.json"
                )
                job_info = json.loads(job_info_response['Body'].read().decode('utf-8'))
                video_title = job_info.get('videoTitle', 'YouTube Video')
                
                # Generate summary from transcript
                transcript = status['transcript']
                
                # Store transcript
                s3.put_object(
                    Bucket=TRANSCRIPTIONS_BUCKET,
                    Key=f"{video_id}/transcript.txt",
                    Body=transcript,
                    ContentType='text/plain',
                    ServerSideEncryption="AES256"
                )
                
                # Generate summary
                summary = summarize_text(transcript, video_id, video_title)
                
                if summary:
                    return {
                        'statusCode': 200,
                        'headers': headers,
                        'body': json.dumps({
                            'status': 'completed',
                            'videoId': video_id,
                            'videoTitle': video_title,
                            'summary': summary
                        })
                    }
            except Exception as e:
                print(f"Error processing completed transcription: {str(e)}")
                return {
                    'statusCode': 500,
                    'headers': headers,
                    'body': json.dumps({
                        'status': 'error',
                        'message': 'Error processing transcription'
                    })
                }
        
        # Return status as is
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(status)
        }
    
    # Unknown endpoint
    else:
        return {
            'statusCode': 404,
            'headers': headers,
            'body': json.dumps({'message': f'Endpoint not found: {path}'})
        }