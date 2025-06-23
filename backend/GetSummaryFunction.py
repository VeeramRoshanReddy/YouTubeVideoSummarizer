import json
import boto3
from botocore.exceptions import ClientError

# Initialize S3 client
s3 = boto3.client('s3')

# S3 bucket name
SUMMARIES_BUCKET = 'video-summaries-bucket-youtube-video-summarizer'
TRANSCRIPTIONS_BUCKET = 'video-transcriptions-bucket-youtube-video-summarizer'

# Define CORS headers globally
def get_cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Requested-With",
        "Access-Control-Max-Age": "3600",
        "Access-Control-Allow-Credentials": "true",
        "Content-Type": "application/json"
    }

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
    
    try:
        # Extract video ID from path parameters
        path_parameters = event.get('pathParameters', {}) or {}
        video_id = path_parameters.get('videoId')
        
        if not video_id:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'message': 'Missing videoId in path parameters'})
            }
        
        print(f"Getting summary for video ID: {video_id}")
        
        # Check if summary exists
        try:
            # Get summary from S3
            response = s3.get_object(
                Bucket=SUMMARIES_BUCKET, 
                Key=f"{video_id}/summary.txt"
            )
            summary = response['Body'].read().decode('utf-8')
            
            # Get metadata if available
            try:
                metadata_response = s3.get_object(
                    Bucket=SUMMARIES_BUCKET, 
                    Key=f"{video_id}/metadata.json"
                )
                metadata = json.loads(metadata_response['Body'].read().decode('utf-8'))
                
                return {
                    'statusCode': 200,
                    'headers': headers,
                    'body': json.dumps({
                        'status': 'completed',
                        'videoId': video_id,
                        'videoTitle': metadata.get('videoTitle', 'YouTube Video'),
                        'summary': summary,
                        'sentiment': metadata.get('sentiment', 'neutral'),
                        'timestamp': metadata.get('timestamp')
                    })
                }
            except ClientError:
                # No metadata, just return summary
                return {
                    'statusCode': 200,
                    'headers': headers,
                    'body': json.dumps({
                        'status': 'completed',
                        'videoId': video_id,
                        'summary': summary
                    })
                }
                
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                # Summary doesn't exist, check if there's an ongoing job
                try:
                    job_info_response = s3.get_object(
                        Bucket=TRANSCRIPTIONS_BUCKET,
                        Key=f"{video_id}/job_info.json"
                    )
                    job_info = json.loads(job_info_response['Body'].read().decode('utf-8'))
                    
                    return {
                        'statusCode': 202,
                        'headers': headers,
                        'body': json.dumps({
                            'status': 'processing',
                            'videoId': video_id,
                            'videoTitle': job_info.get('videoTitle', 'YouTube Video'),
                            'jobId': job_info.get('jobName')
                        })
                    }
                except ClientError:
                    # No job info either - need to process the video
                    # Return a response that tells the client to call the processing endpoint
                    return {
                        'statusCode': 200,  # Use 200 instead of 404 to avoid errors
                        'headers': headers,
                        'body': json.dumps({
                            'status': 'not_found',
                            'videoId': video_id,
                            'message': 'Summary not found. Please initiate processing.'
                        })
                    }
            else:
                # Other S3 error
                return {
                    'statusCode': 500,
                    'headers': headers,
                    'body': json.dumps({
                        'message': f'Error accessing S3: {str(e)}'
                    })
                }
                
    except Exception as e:
        print(f"Error in Lambda handler: {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({
                'message': f'Error retrieving summary: {str(e)}'
            })
        }